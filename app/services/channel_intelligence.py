"""
app.services.channel_intelligence — Hệ thống Trí Tuệ Kênh Tự Động (Channel Intelligence & Anti-Duplication).
Đảm bảo:
1. Thuật toán gợi ý ý tưởng thông minh cho từng kênh, kiểm tra tương đồng và chống trùng lặp 100% với video cũ.
2. Báo cáo & giám sát chỉ tiêu KPI định kỳ cho từng kênh (đúng hạn, sắp trễ, trễ deadline >= 2 ngày).
3. Tự động sản xuất video cứu tương tác (Auto-Generation) khi một kênh trễ deadline 2 ngày.
4. Thúc giục người dùng kiểm tra lại video (Urgent Reminder).
5. Cơ chế đăng tạm khẩn cấp (Grace-Period Fallback Publish): Nếu người dùng bận không xác nhận sau thời hạn (ví dụ 4 giờ), tự động đăng/lên lịch vào giờ vàng để giữ vững thuật toán YouTube.
"""

import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

import requests as http_requests

from config.settings import settings
from app.database.models import get_db, VideoJob, User, ActivityLog, log_activity
from app.schemas.contracts import JobStatus, ScriptContract
from app.services.youtube_analytics import fetch_channel_live_stats
from app.services.niche_manager import get_niche, build_script_prompt_instructions
from app.services.smart_scheduler import get_next_available_golden_slot
from app.agents.content_agent import ContentAgent

logger = logging.getLogger(__name__)

# Giới hạn tối đa không có video mới trước khi kích hoạt Auto-Generation khẩn cấp (48 tiếng = 2 ngày)
KPI_CRITICAL_OVERDUE_HOURS = 48.0
# Ngưỡng cảnh báo màu vàng (24 tiếng = 1 ngày)
KPI_WARNING_HOURS = 24.0
# Thời gian chờ duyệt trước khi tự động đăng tạm (mặc định 4 tiếng)
DEFAULT_APPROVAL_GRACE_HOURS = 4.0


# ──────────────────────────────────────────────────────────────────────────────
# 1. THUẬT TOÁN CHỐNG TRÙNG LẶP Ý TƯỞNG (ANTI-DUPLICATION ENGINE)
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_text_for_similarity(text: str) -> str:
    """Làm sạch và chuẩn hoá chuỗi văn bản để so sánh độ tương đồng."""
    if not text:
        return ""
    # Chuyển chữ thường
    clean = text.lower()
    # Chuẩn hóa số có dấu phẩy: ví dụ 11,000 -> 11000
    clean = re.sub(r"(\d+)\s*,\s*(\d+)", r"\1\2", clean)
    # Chuẩn hóa đơn vị đo lường: ví dụ 11000 meters / 11000 meter -> 11000m
    clean = re.sub(r"(\d+)\s*(?:meters|meter|m)\b", r"\1m", clean)
    # Loại bỏ emoji và ký tự đặc biệt
    clean = re.sub(r"[^\w\s]", " ", clean, flags=re.UNICODE)
    # Loại bỏ các từ dừng thông dụng trong tiêu đề Shorts
    stop_words = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "this", "that", "why", "how", "what", "signs", "sign",
        "video", "shorts", "viral", "psychology", "mystery", "history",
        "they", "them", "their", "you", "your", "when", "into", "from",
        "was", "were", "been", "have", "has", "had", "will", "would", "could",
        "những", "các", "và", "của", "là", "trong", "về", "dấu", "hiệu",
        "bí", "ẩn", "tâm", "lý", "thế", "giới"
    }
    tokens = [w.strip() for w in clean.split() if len(w.strip()) >= 2 and w.strip() not in stop_words]
    return " ".join(tokens)


def extract_saturated_channel_concepts(existing_titles: List[str], min_frequency: int = 2) -> List[str]:
    """
    Tác nhân tự động nhận diện các cụm từ, thực thể hoặc chủ đề con bị bão hòa (Fatigued / Saturated Concepts)
    dựa trên lịch sử phát sóng thực tế của kênh, mà KHÔNG CẦN hardcode bất kỳ từ khóa nào.
    
    Phát hiện:
    - Các n-gram (2-3 từ) hoặc thực thể xuất hiện lặp lại >= min_frequency lần trong các video gần đây.
    - Các định lượng / số đo / địa danh lặp lại.
    - Trả về danh sách các khái niệm đang bị bão hòa để Agent chủ động chuyển hướng góc nhìn mới.
    """
    if not existing_titles or len(existing_titles) < 2:
        return []
    
    from collections import Counter
    
    normalized_titles = [_normalize_text_for_similarity(t) for t in existing_titles[:25]]
    phrase_counts = Counter()
    
    for norm in normalized_titles:
        words = norm.split()
        seen_in_title = set()
        
        # 1-gram: Các từ có độ dài >= 4 hoặc các token có chứa số/đơn vị (11000m, 4000psi, etc.)
        for w in words:
            if len(w) >= 4 and not w.isdigit():
                seen_in_title.add(w)
            elif any(c.isdigit() for c in w):
                seen_in_title.add(w)
                
        # 2-grams
        for i in range(len(words) - 1):
            bg = f"{words[i]} {words[i+1]}"
            seen_in_title.add(bg)
            
        # 3-grams
        for i in range(len(words) - 2):
            tg = f"{words[i]} {words[i+1]} {words[i+2]}"
            seen_in_title.add(tg)
            
        for phrase in seen_in_title:
            phrase_counts[phrase] += 1
            
    saturated = [p for p, count in phrase_counts.items() if count >= min_frequency]
    saturated.sort(key=lambda x: (len(x.split()), len(x)), reverse=True)
    
    clean_saturated = []
    for item in saturated:
        if not any(item != other and item in other for other in clean_saturated):
            clean_saturated.append(item)
            
    return clean_saturated[:8]



def check_idea_similarity(
    candidate_title_or_topic: str,
    existing_items: List[str],
    similarity_threshold: float = 0.40
) -> Tuple[bool, float, str]:
    """
    Kiểm tra xem ý tưởng/tiêu đề mới có bị trùng lặp hoặc quá giống với danh sách video đã làm không.
    Sử dụng kết hợp Jaccard Token Overlap và Substring Keyword Containment.
    Trả về: (is_duplicate: bool, max_score: float, matched_item: str)
    """
    if not candidate_title_or_topic or not existing_items:
        return False, 0.0, ""

    norm_candidate = _normalize_text_for_similarity(candidate_title_or_topic)
    cand_tokens = set(norm_candidate.split())
    if not cand_tokens:
        return False, 0.0, ""

    max_score = 0.0
    best_match = ""

    for item in existing_items:
        if not item:
            continue
        norm_item = _normalize_text_for_similarity(item)
        item_tokens = set(norm_item.split())
        if not item_tokens:
            continue

        intersection = cand_tokens.intersection(item_tokens)
        union = cand_tokens.union(item_tokens)

        # 1. Jaccard similarity
        jaccard = len(intersection) / len(union) if union else 0.0

        # 2. Overlap trên tập từ nhỏ hơn (phát hiện ý tưởng bị bao hàm 100%)
        containment = len(intersection) / min(len(cand_tokens), len(item_tokens)) if min(len(cand_tokens), len(item_tokens)) > 0 else 0.0

        # Điểm tương đồng tổng hợp
        combined_score = max(jaccard, containment * 0.75)

        if combined_score > max_score:
            max_score = combined_score
            best_match = item

    is_duplicate = max_score >= similarity_threshold
    return is_duplicate, round(max_score, 3), best_match


def ensure_unique_title(
    candidate_title: str,
    channel_id: str,
    topic: str = "",
    language: str = "en",
    similarity_threshold: float = 0.40
) -> Tuple[str, bool, Optional[str]]:
    """
    Kiểm tra và đảm bảo tiêu đề video độc bản 100%, không bị trùng lặp với bất kỳ video nào trong quá khứ của kênh.
    Nếu phát hiện tương đồng >= similarity_threshold:
    Tự động gọi Gemini AI để biến tấu một tiêu đề hoàn toàn mới lạ (đổi góc nhìn, giữ nguyên chủ đề).
    Trả về: (final_title: str, was_duplicate: bool, matched_previous_title: Optional[str])
    """
    if not candidate_title:
        return candidate_title, False, None

    clean_cand = re.sub(
        r'^(?:(?:hook|title|tiêu\s*đề)\s*)?(?:variant|option|ver|version|biến\s*thể)\s*#?\d+\s*(?:\([^)]*\))?\s*[:\-–—]\s*',
        '',
        candidate_title.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not clean_cand:
        clean_cand = candidate_title.strip()

    portfolio = get_channel_existing_portfolio(channel_id)
    existing_titles = portfolio.get("existing_titles", [])
    if not existing_titles:
        return clean_cand, False, None

    is_dup, score, matched = check_idea_similarity(clean_cand, existing_titles, similarity_threshold=similarity_threshold)
    if not is_dup:
        return clean_cand, False, None

    logger.warning(
        f"[AntiDuplication] ⚠️ Phát hiện tiêu đề trùng lặp: '{clean_cand}' "
        f"(tương đồng {score:.2f} với '{matched}'). Đang tự động đổi tiêu đề độc bản mới..."
    )

    # Dùng Gemini AI để sinh tiêu đề độc bản mới
    keys = settings.get_gemini_keys_pool()
    if keys:
        from app.utils.key_rotator import KeyRotator
        import requests
        rotator = KeyRotator(keys)
        prompt = f"""You are an elite YouTube Shorts copywriter.
The channel already has a video with this title:
"{matched}"

A new video was drafted with a title that is TOO SIMILAR:
"{clean_cand}"
Topic: "{topic or clean_cand}"
Target language: {language}

TASK:
Write ONE completely new, fresh, high-CTR YouTube Shorts title for this topic.
Requirements:
1. MUST NOT use the same sentence structure or key hook words as "{matched}".
2. Approach from a different high-urgency angle (e.g. survival secret, terrifying anomaly, counter-intuitive twist, forbidden discovery).
3. Under 50 characters, punchy, includes 1-2 impactful emojis.
4. Output ONLY the new title as raw text, no quotes, no markdown, no explanation.
"""
        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
        for model, key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=6):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            try:
                res = requests.post(url, json=payload, timeout=12)
                if res.status_code == 200:
                    text = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    text = text.replace('"', '').replace("'", '').strip()
                    if text and len(text) > 5 and not check_idea_similarity(text, [matched], similarity_threshold=0.45)[0]:
                        logger.info(f"[AntiDuplication] ✅ Đã đổi tiêu đề thành công ({model}): '{clean_cand}' -> '{text}'")
                        return text, True, matched
                elif res.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(key, model, status_code=res.status_code, error_text=res.text[:250])
                    continue
            except Exception:
                pass

    # Fallback nếu AI bận: gắn hậu tố tự nhiên (không ghép "Secret Behind" trước một câu mệnh lệnh gây sai ngữ pháp!)
    base_clean = clean_cand.rstrip(".! ")
    fallback_title = (
        f"{clean_cand.split('?')[0].strip()} — The Hidden Truth! 💀"
        if "?" in clean_cand
        else f"{base_clean} (The Hidden Truth) ⚠️"
    )
    return fallback_title, True, matched


# ──────────────────────────────────────────────────────────────────────────────
# 2. THU THẬP DANH MỤC VIDEO & TOP PERFORMER CỦA KÊNH
# ──────────────────────────────────────────────────────────────────────────────

def is_off_niche_video_title(title: str, niche_id: str) -> bool:
    """
    Phát hiện các video bị đăng nhầm ngách trong lịch sử kênh (ví dụ: video bóng đá / tâm lý học
    trên kênh động vật hoang dã hoặc bí ẩn đại dương; hoặc video y tế / tâm lý trên kênh lịch sử)
    để KHÔNG BAO GIỜ lấy làm mẫu Winner Cloner hay gợi ý chủ đề tương tự.
    """
    if not title:
        return False
    t_low = title.lower()
    n_low = (niche_id or "").lower()

    # Từ khóa bóng đá / thể thao (ngoại lai với cả 7 kênh hiện tại)
    football_terms = ("bayern", "rondo", "football", "futebol", "passes que destru", "soccer", "champions league", "messi", "ronaldo")
    if any(k in t_low for k in football_terms):
        return True

    if "wildlife" in n_low or "predator" in n_low:
        # Kênh động vật săn mồi: cấm các tiêu đề tâm lý học xã hội / con người / vũ trụ
        off_wildlife = (
            "smart people", "loud idiots", "narcissist", "manipulat", "gaslight",
            "sociopath", "serial killer", "lie to you", "using you", "interrogat",
            "black hole", "galaxy", "solar system"
        )
        if any(k in t_low for k in off_wildlife):
            return True

    elif "alternate" in n_low or "history" in n_low:
        # Kênh lịch sử giả định: cấm các tiêu đề tâm lý học cá nhân hoặc tiêm chủng/y tế hiện đại
        off_history = (
            "smart people", "loud idiots", "using you", "narcissist", "manipulat",
            "vaccine", "injection", "deltoid", "shoulder muscle", "doctor"
        )
        if any(k in t_low for k in off_history):
            return True

    elif "deep_sea" in n_low or "myster" in n_low:
        off_mystery = ("smart people", "loud idiots", "using you", "narcissist")
        if any(k in t_low for k in off_mystery):
            return True

    return False


def get_channel_existing_portfolio(channel_id: str) -> Dict[str, Any]:
    """
    Thu thập toàn bộ dữ liệu lịch sử nội dung của kênh từ 2 nguồn:
    1. YouTube Live Stats (các video đã công chiếu thực tế trên YouTube).
    2. SQLite VideoJob & PublishedVideo (các video đã được tạo trong hệ thống qua toàn bộ danh sách alias của kênh).
    """
    from app.services.smart_scheduler import resolve_channel_family
    family_ids = resolve_channel_family(channel_id)
    fam_lowers = [f.lower() for f in family_ids]

    cfg_channels = settings.get_channels_config()
    target_cfg = next(
        (c for c in cfg_channels if any(
            ident and str(ident).lower() in fam_lowers
            for ident in (c.get("id"), c.get("youtube_channel_id"), c.get("handle"), c.get("name"))
        )),
        None
    )
    if not target_cfg:
        target_cfg = next((c for c in cfg_channels if c.get("id") == channel_id or c.get("handle") == channel_id or c.get("name") == channel_id), {
            "id": channel_id,
            "name": channel_id,
            "niche": "psychology",
            "language": "en"
        })

    handle_or_url = target_cfg.get("handle") or target_cfg.get("channel_url") or channel_id
    channel_name = target_cfg.get("name") or channel_id
    channel_niche = target_cfg.get("niche") or "general"
    channel_lang = target_cfg.get("language") or "en"

    existing_titles: List[str] = []
    existing_topics: List[str] = []
    top_performers: List[Dict[str, Any]] = []
    flop_videos: List[Dict[str, Any]] = []
    last_activity_time: Optional[datetime] = None

    # A0. Đọc dữ liệu từ file audit kênh mới nhất (storage/channel_audit_latest.json)
    try:
        audit_file = Path(__file__).resolve().parent.parent.parent / "storage" / "channel_audit_latest.json"
        if audit_file.exists():
            audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
            matched_audit = audit_data.get(channel_id)
            if not matched_audit:
                for k, v in audit_data.items():
                    if (
                        v.get("id") == channel_id
                        or (v.get("handle") and v.get("handle").lower() == str(handle_or_url).lower())
                        or (v.get("name") and v.get("name").lower() == str(channel_name).lower())
                    ):
                        matched_audit = v
                        break

            if matched_audit:
                raw_vids = matched_audit.get("videos") or []
                if not raw_vids and matched_audit.get("live_scraper_stats"):
                    raw_vids = matched_audit.get("live_scraper_stats", {}).get("videos", [])

                audit_vids_parsed = []
                for v in raw_vids:
                    t = (v.get("title") or "").strip()
                    if not t:
                        continue
                    if t not in existing_titles:
                        existing_titles.append(t)
                    
                    v_views = v.get("views") if v.get("views") is not None else v.get("views_num", 0)
                    try:
                        v_views = int(v_views)
                    except Exception:
                        v_views = 0

                    audit_vids_parsed.append({
                        "title": t,
                        "views_num": v_views,
                        "views_text": v.get("views_text") or f"{v_views} lượt xem",
                        "url": v.get("url") or f"https://youtube.com/shorts/{v.get('id', '')}"
                    })

                if audit_vids_parsed:
                    # Sắp xếp lượt xem giảm dần
                    audit_vids_parsed.sort(key=lambda x: x["views_num"], reverse=True)
                    on_niche_vids = [pv for pv in audit_vids_parsed if not is_off_niche_video_title(pv["title"], channel_niche)]
                    best_on_niche_views = on_niche_vids[0]["views_num"] if on_niche_vids else 0
                    # Video đỉnh cao (views >= 100 hoặc video đúng ngách top 1 có views > 15)
                    for pv in on_niche_vids:
                        if pv["views_num"] >= 100 or (pv["views_num"] == best_on_niche_views and pv["views_num"] > 15):
                            if pv not in top_performers:
                                top_performers.append(pv)
                    # Video flop hoặc video lệch ngách (cần tuyệt đối né tránh lặp lại)
                    for pv in reversed(audit_vids_parsed):
                        if (pv["views_num"] <= 25 or is_off_niche_video_title(pv["title"], channel_niche)) and pv not in flop_videos and pv not in top_performers:
                            flop_videos.append(pv)
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi đọc channel_audit_latest.json cho {channel_id}: {e}")

    # A. Cào dữ liệu Live từ YouTube
    yt_stats = {}
    try:
        yt_stats = fetch_channel_live_stats(handle_or_url)
        if yt_stats.get("success"):
            for vid in yt_stats.get("videos", []):
                t = vid.get("title", "").strip()
                if t and t not in existing_titles:
                    existing_titles.append(t)
                v_num = vid.get("views_num", 0)
                off_niche = is_off_niche_video_title(t, channel_niche)
                if not off_niche and (vid.get("is_top_performer") or v_num >= 300):
                    top_obj = {
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    }
                    if not any(x["title"] == t for x in top_performers):
                        top_performers.append(top_obj)
                elif (v_num < 30 or off_niche) and not any(x["title"] == t for x in flop_videos):
                    flop_videos.append({
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    })
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi lấy stats YouTube cho {channel_id}: {e}")

    # B. Lấy dữ liệu từ SQLite DB (Toàn bộ họ kênh / alias)
    try:
        with get_db() as db:
            jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(family_ids)
            ).order_by(VideoJob.created_at.desc()).all()

            for j in jobs:
                if j.title and j.title not in existing_titles:
                    existing_titles.append(j.title)
                if j.topic and j.topic not in existing_topics:
                    existing_topics.append(j.topic)

            from app.database.models import PublishedVideo
            pub_vids = db.query(PublishedVideo).filter(
                PublishedVideo.channel_id.in_(family_ids)
            ).all()
            for pv in pub_vids:
                if pv.title and pv.title not in existing_titles:
                    existing_titles.append(pv.title)

            # Lấy mốc thời gian video mới nhất
            # Ưu tiên video có trạng thái hoàn tất (RENDERED, SCHEDULED, PUBLISHED, AWAITING_APPROVAL)
            active_jobs = [j for j in jobs if j.status in ("RENDERED", "SCHEDULED", "PUBLISHED", "AWAITING_APPROVAL")]
            if active_jobs:
                last_activity_time = active_jobs[0].updated_at or active_jobs[0].created_at
            elif jobs:
                last_activity_time = jobs[0].created_at
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi truy vấn DB cho {channel_id}: {e}")

    now_utc = datetime.utcnow()
    if last_activity_time:
        delta = now_utc - last_activity_time
        hours_since_last_video = max(0.0, delta.total_seconds() / 3600.0)
        days_since_last_video = max(0.0, delta.total_seconds() / 86400.0)
    else:
        # Thử lấy thời gian video mới nhất từ YouTube Data API (nếu kênh có token cấu hình)
        try:
            from app.agents.publisher_agent import PublisherAgent
            channels_list = settings.get_channels_config()
            matched_cfg = None
            for ch_item in channels_list:
                if ch_item.get("id") == channel_id or ch_item.get("handle") == handle_or_url:
                    matched_cfg = ch_item
                    break
            if matched_cfg:
                pub = PublisherAgent(channel_config=matched_cfg)
                if pub.service:
                    ch_res = pub.service.channels().list(mine=True, part="contentDetails").execute()
                    if ch_res.get("items"):
                        up_id = ch_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
                        pl_res = pub.service.playlistItems().list(playlistId=up_id, part="snippet", maxResults=1).execute()
                        if pl_res.get("items"):
                            p_at = pl_res["items"][0]["snippet"]["publishedAt"]
                            dt = datetime.fromisoformat(p_at.replace("Z", "+00:00")).replace(tzinfo=None)
                            last_activity_time = dt
                            delta = now_utc - last_activity_time
                            hours_since_last_video = max(0.0, delta.total_seconds() / 3600.0)
                            days_since_last_video = max(0.0, delta.total_seconds() / 86400.0)
        except Exception as e_yt:
            logger.debug(f"[Portfolio] Không thể lấy thời gian thực từ YouTube API cho {channel_id}: {e_yt}")

    # Fallback nếu vẫn chưa xác định được last_activity_time
    if not last_activity_time:
        yt_vids_count = yt_stats.get("videos_count_num", 0) if isinstance(yt_stats, dict) else 0
        if yt_vids_count > 0:
            # Có video trên YouTube nhưng chưa có log DB -> giả định đã trễ 52 giờ để AI kích hoạt cứu tương tác
            hours_since_last_video = 52.0
            days_since_last_video = 2.1
            last_activity_time = now_utc - timedelta(hours=52)
        else:
            # Kênh hoàn toàn mới, chưa có video
            hours_since_last_video = 120.0
            days_since_last_video = 5.0
            last_activity_time = now_utc - timedelta(days=5)

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_niche": channel_niche,
        "channel_lang": channel_lang,
        "handle_or_url": handle_or_url,
        "existing_titles": existing_titles,
        "existing_topics": existing_topics,
        "top_performers": top_performers,
        "flop_videos": flop_videos,
        "last_activity_time": last_activity_time,
        "hours_since_last_video": round(hours_since_last_video, 1),
        "days_since_last_video": round(days_since_last_video, 2),
        "total_existing_count": len(existing_titles)
    }


def _get_global_trends_directive(niche_id: str) -> str:
    """Cung cấp xu hướng Shorts toàn cầu 2026 thịnh hành nhất theo từng ngách cụ thể."""
    nid = (niche_id or "").lower()
    if any(k in nid for k in ("deep_sea", "ocean", "thalassophobia", "bien_sau", "abyss", "mysteries")):
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (MYSTERIES OF THE WORLD, SENSORY PARADOXES & DEEP ANOMALIES):
1. Human Sensory & Biological Blind Spots (Proven Breakout on Channel - 140 to 190 Views, 89% APV): Why your eyes are biologically blind to 99% of reality; why the human mind starts hallucinating inside the world's quietest room (Orfield Anechoic Chamber) in 15 minutes; the Phantom Limb / Capgras neural paradox.
2. Instinctive Thalassophobia & Unexplained Acoustic Signals (Proven #1 Winner - 1,242 Views): Why human DNA carries a primal fear of dark water; NOAA's classified hydrophone recordings (The Bloop, Julia, Upsweep).
3. Sealed Subterranean Vaults & Forbidden Ecosystems: Movile Cave (sealed for 5.5 million years breathing toxic sulfur), Lake Vostok beneath 4km of Antarctic ice, Derinkuyu underground city.
CRITICAL ANTI-CANNIBALIZATION CONSTRAINT: STRICTLY FORBIDDEN to use the overused phrase "11,000m", "11,000 meters", or "Mariana Trench"! Pivot to human sensory anomalies, sealed subterranean ecosystems, and acoustic mysteries!
"""
    elif any(k in nid for k in ("alternate_history", "what_if", "timeline", "temporal")):
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (ALTERNATE HISTORY & WHAT IF?):
1. Explicit "What If...?" Counterfactual Titles + Modern Reality Anchor: Viewers swipe away from dry textbook history titles ("How A Rainstorm Saved Europe" got 8 views). Every title MUST lead with a high-curiosity "What If...?" question or terrifying butterfly paradox ("What If the Library of Alexandria Never Burned?", "What If the Titanic Never Hit the Iceberg?").
2. 2nd-Person Modern Reality Hook (0-3s): Anchor the ancient turning point directly to the viewer's life in 2026 ("Look at your smartphone — if one fire in 48 BC had been stopped, human beings would have landed on Mars by 1650").
3. 3-Stage Butterfly Domino Effect (Mid-Video Re-Hook at 12s): Show step-by-step how 1 changed decision rewrites borders, languages, and technology to prevent the 13-second retention drop-off.
STRICT CONSTRAINT: Strictly 100% historical counter-factual scenarios ("What If...") and empire turning points. ZERO psychology advice, ZERO true-crime/serial killer topics, ZERO modern medical/vaccine/injection topics, ZERO violent clickbait words ("Bloodbath", "Execution Order", "Wiped Out")!
Hook Formula: 'Look at the world around you — if one single decision in [Year] went the other way, today you would...'
"""
    elif "mystery" in nid or "history" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (MYSTERIES & ANOMALIES):
1. Subterranean & Historical Acoustic Anomalies: Ancient acoustic resonance chambers (Kailasa, Hal Saflieni Hypogeum, Puma Punku), declassified signals with exact frequency stats.
2. Forbidden Architecture & Sealed Vaults: Underground secrets beneath the Sphinx's right paw, Derinkuyu subterranean ventilation mysteries, Vatican secret vault micro-documents.
3. Glitches in Historical Reality: Prophecies with eerie modern precision (Roger Bacon 1290 machines), ancient astronomical alignments.
Hook Formula: 'In [Year], archaeologists uncovered a sealed chamber that official records erased...'
"""
    elif "animal" in nid or "rescue" in nid or "pet" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (ANIMAL RESCUE & EMOTIONAL HEALING):
1. Curiosity + Refusal To Give Up (Proven #1 Channel Winner - 2,282 Views & +32 Subs): 'He Refused to Let Go Until This Happened...' — Combining an intriguing opening mystery with a 50-second 4-stage emotional transformation arc.
2. The Exact Breakthrough Moment (Proven #2 Winner - 1,205 Views & 94.8% APV): The exact second a terrified rescue puppy stopped shaking and dared to rest its head on the rescuer's hand.
3. Joyful Recovery Payoff (45-56s): Full emotional journey from abandonment -> gentle rescue -> veterinary healing -> joyous sprint in a sunlit meadow.
CRITICAL CONSTRAINT: STRICTLY FORBIDDEN to use bleak/depressing-only titles ('Frozen Puppy Found in Rain', 'Shivering Puppy Tied In Rain'). Every title MUST contain a curiosity gap or promise of transformation ('Until This Happened', 'The Exact Moment He Realized He Was Safe')!
Hook Formula: 'He refused to let go of the rescuer's hand until they looked inside the box...'
"""
    elif "wildlife" in nid or "predator" in nid or "nature" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (WILDLIFE & APEX PREDATORS):
1. Head-to-Head Apex Predator Duels (Proven #1 Breakout Formula - 214 Views on Debut): Green Anaconda vs. Black Caiman; Orca vs. Great White Shark; Honey Badger vs. Lion Pride; Siberian Tiger vs. Grizzly Bear.
2. Extreme Asymmetric Combat Biomechanics: Jaguar 1,500 PSI skull-crushing bite vs. armored caiman; Mantis Shrimp cavitation punch; Peregrine Falcon 240 mph dive physics.
STRICT NEGATIVE CONSTRAINT: Strictly 100% biological animal predator behaviors and head-to-head matchups. ZERO sports/football teams (NO 'Bayern'), zero space facts, zero psychology!
Hook Formula: 'Green Anaconda vs. Black Caiman — when these two river monsters collide, only one leaves alive...'
"""
    else:  # psychology / dark psychology / self-mastery
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (DARK PSYCHOLOGY & HUMAN BEHAVIOR):
1. Criminal & Manipulator Micro-Expressions (Proven 941-View Winner): Why serial killers and covert manipulators smile or freeze their eyes in specific high-stress moments.
2. Personal Threat & Social Traps (Proven 514-View & 156% APV Winner): '3 Signs They're JUST Using You', 'The Deadliest Trap in Any Room'.
3. High-Status Unblinking Silence: How calm, deliberate non-reaction flips power dynamics during public disrespect or interrogation.
Hook Formula: 'If someone does this within the first 5 seconds of speaking to you, pay close attention...'
"""


# ──────────────────────────────────────────────────────────────────────────────
# 2.5. BỘ NHÂN BẢN QUÁN QUÂN & PHÁT HIỆN SERIES PART 2 (WINNER CLONER ENGINE)
# ──────────────────────────────────────────────────────────────────────────────

def get_winner_cloner_blueprint(
    channel_id: str,
    niche_id: Optional[str] = None,
    portfolio: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Phân tích video quán quân (Outlier Winner) của kênh để trích xuất:
    1. Cấu trúc cú pháp tiêu đề chiến thắng (Winner Title Syntax Pattern).
    2. Danh sách chủ đề 'Sister Sequel / Part 2' nhân bản cơ chế tâm lý của video thắng lớn
       nhưng đổi sang thực thể/tình huống mới 100% (đã lọc chống trùng lặp qua check_idea_similarity).
    3. Danh sách chủ đề chưa từng dùng từ `viral_topic_bank_2026` của Niche YAML.
    """
    port = portfolio or get_channel_existing_portfolio(channel_id)
    eff_niche = (niche_id or port.get("channel_niche") or "psychology").lower()
    existing_titles = list(port.get("existing_titles") or []) + list(port.get("existing_topics") or [])
    top_performers = port.get("top_performers") or []

    niche_prof = get_niche(eff_niche)
    blueprint_cfg = niche_prof.get("script_blueprint") or {}
    yaml_topic_bank = list(blueprint_cfg.get("viral_topic_bank_2026") or [])

    winner_title = top_performers[0]["title"] if top_performers else ""
    winner_views = top_performers[0].get("views_num", 0) if top_performers else 0

    # Kho Sister Sequel theo từng mẫu Winner thực tế của 7 kênh (5 US + 2 Brazil pt-BR)
    sequel_templates: List[str] = []
    w_low = winner_title.lower()
    is_pt = (
        str(port.get("channel_lang") or "").lower().startswith("pt")
        or str(channel_id).lower() in ("ch_odisseia_selvagem", "ch_misterios_do_mundo", "@odisseiaselvagemoficial", "@misteriosdomundo-tv")
    )

    if is_pt:
        if "wildlife" in eff_niche or "predator" in eff_niche or "odisseia" in str(channel_id).lower():
            sequel_templates = [
                "Ariranha vs Jacaré no Pantanal: O Bando Sanguinário Que Até a Onça Respeita! 🦦🐊",
                "Onça-Pintada vs Jacaré-Açu: A Mordida de 1.500 PSI Que Perfura Crânios na Água! 🐆💥",
                "Harpia Brasileira: A Águia Monstruosa Com Garras do Tamanho de um Urso Pardo! 🦅🔥",
                "Piranha-Preta vs Poraquê de 860 Volts: O Duelo Mais Letal dos Rios da Amazônia! ⚡🐟",
                "Sucuri Gigante de 8 Metros vs Jacaré-Açu: Quem Domina as Águas da Amazônia? 🐍🐊",
                "Texugo-do-Mel vs Orgulho de Leões: O Animal Que Não Sente Medo e Nem Veneno! 🦡🦁",
                "Orca vs Tubarão-Branco: O Golpe Cirúrgico Que Faz o Rei do Mar Fugir em Pânico! 🐋🦈",
            ]
        else:
            # Mistérios do Mundo (Winner: "Sons bizarros do fundo do oceano que ninguém explica! 🌊😱" - 1,435 views, 50 likes)
            sequel_templates = [
                "O Som Mais Aterrorizante Já Gravado no Ponto Nemo — E a Ciência Escondeu! 🌊😱",
                "Por Que os Cientistas Desligaram os Microfones no Fundo do Oceano Pacífico? 🎧🌊",
                "Ilha da Queimada Grande: Por Que a Marinha do Brasil Proíbe Qualquer Humano de Pisar Lá? 🐍🚫",
                "O Rio Que Ferve a 93°C no Coração da Amazônia — Tudo Que Cai Lá Dentro Cozinha Vivo! 🔥🌿",
                "A Caverna Selada Há 5 Milhões de Anos Onde Criaturas Vivem Sem Oxigênio e Sem Luz! 🕳️🧬",
                "Por Que Seus Olhos São Biologicamente Cegos Para 99% do Que Está Ao Seu Redor? 👁️🧠",
                "A Sala Mais Silenciosa do Mundo Onde Ninguém Aguenta Ficar 15 Minutos Sem Alucinar! 🔇🧠",
            ]
        yaml_topic_bank = []
    elif "psychology" in eff_niche:
        sequel_templates = [
            "Why FBI Interrogators Watch Your Feet Instead Of Your Eyes When You Lie",
            "Why Guilty Suspects Always Repeat Your Question Before Answering In Police Rooms",
            "The 1971 Stanford Prison Experiment: How Ordinary Students Turned Ruthless In 6 Days",
            "Why Sociopaths Never Blink When They Lie Under Oath In Court",
            "The Bystander Paradox: Why 30 People Freeze When Someone Screams For Help",
        ]
    elif "rescue" in eff_niche or "animal" in eff_niche:
        sequel_templates = [
            "The Stray Puppy Who Grabbed A Rescuer's Coat And Led Them To A Storm Drain",
            "Terrified Shelter Dog Hid Facing The Wall Until One Rescuer Sat Quietly Beside Him",
            "Hero Stray Dog Stood Guard Over A Box Of Orphaned Kittens In Pouring Rain",
            "Everyone Walked Past The Senior Shelter Dog Until One Man Read His Sign",
        ]
    elif "deep_sea" in eff_niche or "myster" in eff_niche:
        sequel_templates = [
            "The Door To Hell: Why This Giant Desert Fire Crater Has Burned Nonstop Since 1971",
            "The Boiling River Of The Amazon: A 200°F River That Boils Anything That Falls In",
            "Why Scientists Welded Shut The Kola Superdeep Borehole At 40,230 Feet",
            "Why Deep Sea Divers Never Look Up At The Surface At Night",
        ]
    elif "wildlife" in eff_niche or "predator" in eff_niche:
        sequel_templates = [
            "Harpy Eagle vs Tree Sloth: The 500 PSI Talons Larger Than Grizzly Bear Claws",
            "Komodo Dragon vs Water Buffalo: How One Venomous Bite Takes Down A 1,200lb Giant",
            "Giant River Otter Pack vs Black Caiman: The Ruthless Gang Of The Amazon",
            "Honey Badger vs King Cobra: How It Sleeps Off Lethal Neurotoxin And Keeps Eating",
        ]
    elif "alternate" in eff_niche or "history" in eff_niche:
        sequel_templates = [
            "What If Vasili Arkhipov Pressed The Nuclear Button Inside Submarine B-59 In 1962?",
            "What If The 3 Chernobyl Suicide Divers Failed To Drain The Reactor Pool In 1986?",
            "What If The 1983 Soviet Satellite Glitch Triggered A Full Nuclear Launch?",
            "What If The 1815 Mount Tambora Super-Eruption Lasted One More Year?",
            "What If The Library Of Alexandria Never Burned Down? (300-Year Tech Leap)",
        ]

    # Gộp sequel_templates + yaml_topic_bank và lọc chống trùng lặp với lịch sử kênh
    fresh_candidates: List[str] = []
    for cand in sequel_templates + yaml_topic_bank:
        if not cand or cand in fresh_candidates:
            continue
        is_dup, _, _ = check_idea_similarity(cand, existing_titles, similarity_threshold=0.38)
        if not is_dup:
            fresh_candidates.append(cand)

    prompt_directive = ""
    if winner_title:
        prompt_directive = (
            f"🔥 WINNER CLONER DIRECTIVE (Top Channel Winner: \"{winner_title}\" — {winner_views:,} views):\n"
            f"  - Replicate the exact high-stakes curiosity tension, 2nd-person visceral framing, and 4-beat payoff of this winner.\n"
            f"  - Recommended 'Part 2 / Sister Sequel' angles ready for production: "
            + "; ".join(f'"{c}"' for c in fresh_candidates[:3])
        )
    elif fresh_candidates:
        prompt_directive = (
            f"🔥 2026 MILLION-VIEW TOPIC BANK RECOMMENDATIONS:\n  - "
            + "\n  - ".join(f'"{c}"' for c in fresh_candidates[:4])
        )

    return {
        "winner_title": winner_title,
        "winner_views": winner_views,
        "fresh_sequel_topics": fresh_candidates,
        "prompt_directive": prompt_directive,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. GỢI Ý Ý TƯỞNG THÔNG MINH CHO TỪNG KÊNH (CHANNEL TOPIC SUGGESTER)
# ──────────────────────────────────────────────────────────────────────────────

def suggest_channel_topics(
    channel_id: str,
    count: int = 5,
    exclude_additional: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Thuật toán AI gợi ý ý tưởng làm video riêng biệt cho từng kênh:
    - Bám sát hồ sơ ngách (Niche Profile: Tâm lý, Bí ẩn, Động vật, v.v.).
    - Học tập và phát huy từ các video thành công nhất của kênh (Top Performers).
    - Khắc phục triệt để các nhược điểm / bẫy flop từ video trước (Topic Fatigue, Niche Confusion, Passive tone).
    - Đón đầu xu hướng toàn cầu năm 2026 theo từng ngách.
    - LỌC TRÙNG LẶP TUYỆT ĐỐI (Strict De-duplication): Kiểm tra qua check_idea_similarity với mọi video cũ.
    """
    portfolio = get_channel_existing_portfolio(channel_id)
    niche_id = portfolio["channel_niche"]
    niche_profile = get_niche(niche_id)
    channel_name = portfolio["channel_name"]
    lang = portfolio["channel_lang"]
    top_performers = portfolio.get("top_performers", [])
    flop_videos = portfolio.get("flop_videos", [])

    all_existing = list(portfolio["existing_titles"])
    if exclude_additional:
        all_existing.extend(exclude_additional)

    # [FIX Phase 2] Thêm các topic đã dùng gần đây từ UsedTopic DB (60 ngày) vào exclusion list
    try:
        from app.database.models import UsedTopic, get_db
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=60)
        from app.services.smart_scheduler import resolve_channel_family
        family_ids = resolve_channel_family(channel_id)
        with get_db() as _db:
            recent_used = _db.query(UsedTopic).filter(
                UsedTopic.channel_id.in_(family_ids),
                UsedTopic.used_at >= cutoff
            ).order_by(UsedTopic.used_at.desc()).limit(100).all()
            db_used_topics = [u.topic_text for u in recent_used]
        if db_used_topics:
            all_existing.extend(db_used_topics)
            logger.debug(
                f"[TopicSuggester] Đã tải {len(db_used_topics)} topic gần đây từ DB deduplication "
                f"cho kênh {channel_id} (60 ngày qua)."
            )
    except Exception as _dedup_err:
        logger.debug(f"[TopicSuggester] Không thể tải UsedTopic từ DB: {_dedup_err}")

    # Xây dựng danh sách video cũ để AI né tránh
    clean_existing_prompt = "\n".join([f"- \"{t}\"" for t in all_existing[:15]])
    if not clean_existing_prompt:
        clean_existing_prompt = "(Chưa có video nào — đây là video khởi đầu của kênh!)"

    winner_blueprint = get_winner_cloner_blueprint(channel_id, niche_id=niche_id, portfolio=portfolio)
    winner_cloner_prompt = winner_blueprint.get("prompt_directive") or ""

    # Xây dựng dữ liệu Top Performers làm hình mẫu lan toả
    top_performer_prompt = ""
    if top_performers:
        top_lines = []
        for p in top_performers[:3]:
            v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
            top_lines.append(f"- \"{p['title']}\" ({v_txt})")
        top_performer_prompt = f"""
🏆 PROVEN HIGH-RETENTION BREAKTHROUGH VIDEOS ON THIS CHANNEL:
{chr(10).join(top_lines)}
Winning Mechanism: High-curiosity primal trigger, instant pattern interrupt, urgent psychological or existential stakes.
Your new ideas MUST inherit this winning emotional DNA without copying the same subject!
{winner_cloner_prompt}
"""
    elif winner_cloner_prompt:
        top_performer_prompt = f"\n{winner_cloner_prompt}\n"

    # Xây dựng dữ liệu Flop Videos và các cạm bẫy cần tránh
    flop_prompt = ""
    if flop_videos:
        flop_lines = []
        for p in flop_videos[:4]:
            v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
            flop_lines.append(f"- \"{p['title']}\" ({v_txt})")
        flop_prompt = f"""
⚠️ UNDERPERFORMING / FLOP VIDEOS ON THIS CHANNEL (CRITICAL TRAPS TO AVOID):
{chr(10).join(flop_lines)}
Core Weaknesses Identified:
1. Topic fatigue: Do NOT repeat the exact same sub-topic or keyword consecutively (e.g., avoid repetitive deep sea ocean lists).
2. Passive / philosophical / low-urgency openings lacking a 3-second pattern interrupt.
3. Niche mismatch: Stay 100% faithful to '{niche_id}', never confuse YouTube's recommendation algorithm with off-niche topics.
Mandate: Each new idea MUST actively solve these weaknesses!
"""

    global_trends_prompt = _get_global_trends_directive(niche_id)

    # Tác nhân tự động nhận diện các cụm từ, số đo hoặc chủ đề đang bị bão hòa
    saturated_concepts = extract_saturated_channel_concepts(all_existing, min_frequency=2)
    saturation_prompt = ""
    if saturated_concepts:
        sat_str = ", ".join([f"\"{c}\"" for c in saturated_concepts])
        saturation_prompt = f"""
═══════════════════════════════════════════════════════════
🚨 AUTONOMOUS REPETITION & CONCEPT FATIGUE WARNING (DETECTED FROM RECENT UPLOADS):
The following specific sub-topics, numbers, entities, or phrasing have appeared repeatedly on this channel:
[{sat_str}]

AUTONOMOUS AGENT MANDATE:
YouTube's recommendation algorithm aggressively penalizes channels that repeatedly publish videos revolving around the same recurring numbers, locations, or phrasing (Topic Cannibalization).
You must AUTONOMOUSLY PIVOT to completely fresh, unvisited premises, alternative entities, or different mechanisms in '{niche_id}'.
DO NOT center any candidate around these fatigued terms!
"""

    prompt = f"""You are the Master Creative Director and Viral Algorithm Strategist for YouTube Shorts channel '{channel_name}'.
Niche: '{niche_profile.get('name')}' ({niche_id})
Target Language: {lang}

═══════════════════════════════════════════════════════════
🚫 STRICT ANTI-DUPLICATION RULE (HIGHEST PRIORITY):
The following titles and concepts ALREADY EXIST on this channel:
{clean_existing_prompt}

CRITICAL MANDATE:
- DO NOT duplicate, rephrase, or lightly modify ANY of the existing topics above!
- You MUST pioneer FRESH, UNEXPLORED angles, psychological paradoxes, or shocking historical mysteries within '{niche_id}'.
═══════════════════════════════════════════════════════════
{top_performer_prompt}
═══════════════════════════════════════════════════════════
{flop_prompt}
═══════════════════════════════════════════════════════════
{global_trends_prompt}
═══════════════════════════════════════════════════════════
{saturation_prompt}
═══════════════════════════════════════════════════════════
🎯 OBJECTIVE:
Generate exactly {count + 2} completely unique, high-retention Shorts concepts tailored specifically to '{channel_name}'.
Each concept must:
1. Inherit the winning emotional hook of top performers.
2. Actively eliminate and fix the channel's past flop weaknesses (topic fatigue, passive tone, niche dilution).
3. Align directly with 2026 global viral trends for this niche.
4. Keep YouTube Shorts titles concise (under 60 characters), punchy, and include #Shorts.

Return ONLY a valid JSON array of objects with the following structure:
[
  {{
    "topic": "Core punchy topic concept (e.g. The 3-second silence interrogation trick)",
    "title": "Viral YouTube Shorts Title with Emojis and #Shorts (under 60 chars)",
    "hook": "Electric 3-second pattern interrupt opening line (5-8 words)",
    "angle": "Micro-narrative breakdown & specific observable behaviors or phenomena",
    "pinned_comment": "Provocative engagement bait question to pin at top of comments",
    "viral_formula": "Double Tap & Protect | Debate in Comments | Curiosity Part 2",
    "predicted_retention": "150% - 165%",
    "why_viral": "Giải thích ngắn gọn bằng tiếng Việt vì sao chủ đề này sẽ bùng nổ views",
    "fixes_flaw": "Giải thích bằng tiếng Việt cách ý tưởng này khắc phục nhược điểm/lỗi flop của kênh (ví dụ: tránh bão hòa biển sâu, tăng tính giật gân, bám đúng ngách)",
    "trend_alignment": "Xu hướng toàn cầu 2026 được đón đầu (ví dụ: Giải mã âm thanh dưới đáy biển / Biomechanics mãnh thú / Dark Triad body language)"
  }}
]
"""
    agent = ContentAgent()
    verified_ideas: List[Dict[str, Any]] = []

    try:
        raw_text = agent._call_api(prompt, json_mode=False, timeout=30)
        # Làm sạch markdown
        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            lines = clean_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()

        start = clean_text.find("[")
        end = clean_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            clean_text = clean_text[start:end+1]

        candidates = json.loads(clean_text)
        if isinstance(candidates, list):
            for cand in candidates:
                cand_title = cand.get("title", "")
                cand_topic = cand.get("topic", "")
                
                # Tự động loại bỏ ứng viên nếu vẫn lặp lại các khái niệm đang bị bão hòa của kênh
                has_fatigued_term = False
                norm_cand = _normalize_text_for_similarity(f"{cand_title} {cand_topic}")
                for sat_term in saturated_concepts:
                    if sat_term in norm_cand:
                        has_fatigued_term = True
                        logger.info(f"[AutonomousFatigueGuard] Bỏ qua ý tưởng '{cand_title}' vì chứa khái niệm đang bị bão hòa '{sat_term}' (tự động nhận diện từ lịch sử kênh)!")
                        break
                if has_fatigued_term:
                    continue

                # Kiểm tra trùng lặp với kho video cũ
                is_dup_title, score_title, match_t = check_idea_similarity(cand_title, all_existing, similarity_threshold=0.38)
                is_dup_topic, score_topic, match_p = check_idea_similarity(cand_topic, all_existing, similarity_threshold=0.38)

                if is_dup_title or is_dup_topic:
                    logger.info(f"[DeDuplication] Bỏ qua ý tưởng trùng lặp: '{cand_title}' (tương đồng {max(score_title, score_topic):.2f} với '{match_t or match_p}')")
                    continue

                # Kiểm tra trùng lặp chéo với chính các ý tưởng vừa thêm
                current_accepted_titles = [x["title"] for x in verified_ideas]
                is_self_dup, _, _ = check_idea_similarity(cand_title, current_accepted_titles, similarity_threshold=0.45)
                if is_self_dup:
                    continue

                cand["channel_id"] = channel_id
                cand["channel_name"] = channel_name
                cand["niche"] = niche_id
                cand["similarity_score"] = max(score_title, score_topic)
                if not cand.get("fixes_flaw"):
                    cand["fixes_flaw"] = "Khắc phục tình trạng bão hòa nội dung cũ, tăng tính kích thích tò mò 3 giây đầu."
                if not cand.get("trend_alignment"):
                    cand["trend_alignment"] = "Đón đầu trào lưu Shorts 2026 với cấu trúc loop vô tận và hook thị giác."
                verified_ideas.append(cand)

                if len(verified_ideas) >= count:
                    break
    except Exception as e:
        logger.error(f"[TopicSuggester] Lỗi gọi AI gợi ý ý tưởng cho {channel_id}: {e}")

    # Fallback an toàn nếu AI gặp sự cố quota hoặc không đủ số lượng
    if len(verified_ideas) < count:
        logger.info(f"[TopicSuggester] Bổ sung fallback curated cho kênh {channel_id} (niche: {niche_id})")
        fallbacks = _get_curated_niche_fallbacks(niche_id, lang)
        for fb in fallbacks:
            fb_title = fb.get("title", "")
            is_dup, score, _ = check_idea_similarity(fb_title, all_existing, similarity_threshold=0.35)
            if not is_dup:
                fb_copy = dict(fb)
                fb_copy["channel_id"] = channel_id
                fb_copy["channel_name"] = channel_name
                fb_copy["niche"] = niche_id
                fb_copy["similarity_score"] = score
                verified_ideas.append(fb_copy)
                if len(verified_ideas) >= count:
                    break

    return verified_ideas[:count]


def _get_curated_niche_fallbacks(niche_id: str, lang: str = "en") -> List[Dict[str, Any]]:
    """Kho ý tưởng dự phòng chất lượng cao cho từng ngách."""
    if "history" in niche_id or "what_if" in niche_id:
        return [
            {
                "topic": "Vasili Arkhipov and the 1962 Soviet Submarine B-59 Nuclear Torpedo Veto",
                "title": "One Man Refused The Order To End The World ☢️🚢 #Shorts",
                "hook": "On October 27, 1962, two Soviet captains voted to fire a nuclear torpedo at the US Navy.",
                "angle": "Submarine B-59 depth charges, 3-officer launch protocol, Vasili Arkhipov's lone veto",
                "pinned_comment": "If Vasili Arkhipov hadn't been on board B-59 that day, would civilization still exist? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "165% - 175%",
                "why_viral": "Khoảnh khắc lịch sử một con người ngăn chặn Thế chiến 3 chỉ bằng 1 lá phiếu phủ quyết",
                "fixes_flaw": "Tập trung vào khoảnh khắc căng thẳng nghẹt thở trong tàu ngầm B-59 năm 1962.",
                "trend_alignment": "Lịch sử bước ngoặt & What-If (Historical Single-Second Turning Points)."
            },
            {
                "topic": "Stanislav Petrov and the 1983 Soviet Nuclear Early-Warning False Alarm",
                "title": "28 Minutes From WW3: The Man Who Disobeyed Protocol 🚨🌍 #Shorts",
                "hook": "In 1983, Soviet satellites flashed five incoming American nuclear missiles in red.",
                "angle": "Oko satellite sunlight glitch, protocol demanded immediate retaliation, Petrov's gut instinct",
                "pinned_comment": "Would you have trusted your gut over a military computer screen? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Quyết định sinh tử của Stanislav Petrov trái lệnh máy tính cứu cả nhân loại năm 1983",
                "fixes_flaw": "Đánh thẳng vào tâm lý nghẹt thở của 1 quyết định lịch sử có thật 100%.",
                "trend_alignment": "Lịch sử chiến tranh lạnh & quyết định định mệnh."
            },
            {
                "topic": "What If The 3 Chernobyl Suicide Divers Failed To Drain The Reactor Pool In 1986?",
                "title": "3 Men Dove Into Radioactive Water To Save Europe ☢️🤿 #Shorts",
                "hook": "In 1986, Chernobyl's melting core was 48 hours away from hitting a giant water tank and wiping out half of Europe.",
                "angle": "Chernobyl thermal explosion risk, Alexei Ananenko and Valeri Bezpalov diving in pitch black radioactive water",
                "pinned_comment": "Why don't school history books teach the names of these 3 men? Debate below 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "165% - 175%",
                "why_viral": "Khoảnh khắc sinh tử có thật 100% với mức độ nguy hiểm huỷ diệt lục địa",
                "fixes_flaw": "Thay thế tiêu đề mơ hồ ('What If This Chauffeur...') bằng tình huống sinh tử hạt nhân rõ ràng ngay giây đầu.",
                "trend_alignment": "Lịch sử bước ngoặt & What-If (Historical Single-Second Turning Points)."
            }
        ]
    elif "mystery" in niche_id or "deep_sea" in niche_id:
        return [
            {
                "topic": "The Door To Hell: Why This Giant Desert Fire Crater Has Burned Nonstop Since 1971",
                "title": "Why Scientists Set This Giant Crater On Fire In 1971 🔥🕳️ #Shorts",
                "hook": "In 1971, Soviet geologists lit a giant gas crater expecting it to burn out in two weeks... it is still burning today.",
                "angle": "Darvaza gas crater collapse in Turkmenistan, 1,800°F methane flames, desert wildlife drawn to the pit",
                "pinned_comment": "How would they ever extinguish a crater burning at 1,800°F? Drop your theory 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Hình ảnh hố lửa khổng lồ cháy suốt 55 năm giữa sa mạc cực kỳ bắt mắt ngay 3 giây đầu",
                "fixes_flaw": "Chuyển từ ảnh tĩnh đáy biển tối đen sang video hố lửa thực tế rực sáng trên YouTube Shorts.",
                "trend_alignment": "Hiện tượng địa chất kỳ bí có thật trên Trái Đất."
            },
            {
                "topic": "The Boiling River Of The Amazon: A 200°F River That Boils Anything That Falls In",
                "title": "The Amazon River That Boils Everything Alive At 200°F 🔥🌿 #Shorts",
                "hook": "Deep in the Peruvian Amazon flows a four-mile river so hot that any animal falling in is boiled alive in seconds.",
                "angle": "Shanay-Timpishka thermal river, 93°C boiling water with no nearby volcano, fault-fed hydrothermal anomaly",
                "pinned_comment": "Would you dare swim near the edge of a 200°F river? 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "155% - 165%",
                "why_viral": "Sự đối lập thị giác giữa rừng rậm Amazon xanh mát và dòng nước sôi bốc khói nghi ngút",
                "fixes_flaw": "Có cảnh quay video thực tế khói bốc nghi ngút trên sông, giữ chân người xem 3.5s đầu.",
                "trend_alignment": "Khám phá địa danh cấm địa kỳ bí trên Trái Đất."
            },
            {
                "topic": "Why Scientists Welded Shut The Kola Superdeep Borehole At 40,230 Feet",
                "title": "Why Scientists Welded Shut The Deepest Hole On Earth 🕳️⚠️ #Shorts",
                "hook": "After drilling 40,230 feet into the Earth's crust, scientists hit a terrifying anomaly and welded the shaft shut forever.",
                "angle": "Kola Superdeep Borehole, 356°F plastic rock behavior, deep microscopic fossils and boiling hydrogen gas",
                "pinned_comment": "What do you think lies beneath 40,000 feet of solid rock? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "150% - 160%",
                "why_viral": "Bí ẩn hố khoan sâu nhất hành tinh bị hàn kín nắp sắt luôn kích thích trí tò mò cực đại",
                "fixes_flaw": "Khai thác bí ẩn lòng đất có hình ảnh nắp hầm kim loại thật và số liệu chiều sâu cụ thể.",
                "trend_alignment": "Bí ẩn lòng đất và công trình khoa học bị phong ấn."
            }
        ]
    elif "animal" in niche_id or "rescue" in niche_id or "pet" in niche_id:
        return [
            {
                "topic": "The Stray Puppy Who Grabbed A Rescuer's Coat And Led Them To A Storm Drain",
                "title": "He Refused To Let Go Until They Followed Him 🥺🐾 #Shorts",
                "hook": "This stray puppy bit onto a stranger's coat and refused to let go for a heartbreaking reason...",
                "angle": "Urgent plea for help, leading rescuer to trapped littermates, full joyful adoption recovery",
                "pinned_comment": "Drop a ❤️ if you believe every stray animal deserves a miracle! 👇",
                "viral_formula": "Emotional Rollercoaster & Tears to Joy",
                "predicted_retention": "170% - 185%",
                "why_viral": "Nhân bản trực tiếp công thức video #1 của kênh 'He Refused to Let Go Until This Happened' (2,282 views, +32 subs)",
                "fixes_flaw": "Thay thế các tiêu đề bi lụy đơn thuần bằng mồi câu tò mò chủ động ('He Refused To Let Go Until...'), kéo dài thời lượng 50-55s có Payoff hạnh phúc.",
                "trend_alignment": "Cấu trúc biến đổi 4 giai đoạn (4-Stage Emotional Transformation Arc) đạt tỷ lệ chuyển đổi Subscribe cao nhất."
            },
            {
                "topic": "Terrified Shelter Dog Hid Facing The Wall Until One Rescuer Sat Quietly Beside Him",
                "title": "The Exact Moment This Broken Dog Dared To Trust Again 🥺❤️ #Shorts",
                "hook": "For three weeks at the shelter, he stared at the concrete wall and trembled whenever anyone walked by...",
                "angle": "Overcoming severe trauma, patient rescuer sitting on floor, first gentle paw touch, joyful tail wag",
                "pinned_comment": "Have you ever rescued an animal? Tell us their name below! 👇",
                "viral_formula": "Double Tap & Healing Empathy",
                "predicted_retention": "165% - 175%",
                "why_viral": "Nhân bản công thức video #2 của kênh (1,205 views, 94.8% APV) — chữa lành tâm hồn",
                "fixes_flaw": "Tập trung vào hành động thực tế của chú chó từ sợ hãi sang chủ động đặt chân lên tay người cứu hộ.",
                "trend_alignment": "Nội dung chữa lành tâm hồn (Healing & Empathy) có tỷ lệ lưu lại (Save) cao nhất trên nền tảng."
            },
            {
                "topic": "Stray Dog Risks Its Own Life to Drag An Injured Kitten Out Of Highway Traffic",
                "title": "Stray Dog RISKS Life to Save A Tiny Kitten 🐕🐱 #Shorts",
                "hook": "Cars were speeding past at 50 mph, but this stray dog saw something moving in the road...",
                "angle": "Cross-species empathy, heroic rescue instinct, inseparable animal brotherhood",
                "pinned_comment": "Animals truly have purer hearts than humans. Agree or disagree? 👇",
                "viral_formula": "Debate in Comments & Heroic Miracle",
                "predicted_retention": "160% - 170%",
                "why_viral": "Câu chuyện động vật cứu nhau luôn đạt triệu view nhờ tính kỳ tích phi thường",
                "fixes_flaw": "Tạo tình huống khẩn cấp giật gân ngay giây đầu tiên, chặn đứng tỷ lệ lướt qua (Swipe Away Rate).",
                "trend_alignment": "Kỳ tích động vật cứu nhau xuyên loài (Cross-species rescue miracle) luôn chạm mốc triệu view thế giới."
            }
        ]
    elif "wildlife" in niche_id or "predator" in niche_id or "nature" in niche_id:
        return [
            {
                "topic": "Harpy Eagle vs Tree Sloth: The 500 PSI Talons Larger Than Grizzly Bear Claws",
                "title": "The Giant Eagle With Claws Bigger Than A Grizzly Bear! 🦅🔥 #Shorts",
                "hook": "With talons larger than a grizzly bear's claws, the Harpy Eagle snatches 20-pound prey straight off tree branches at 50 mph.",
                "angle": "500 PSI crushing grip, canopy radar facial disc, snatching sloths and monkeys mid-flight",
                "pinned_comment": "Is the Harpy Eagle the most terrifying bird on Earth? Drop your vote below! 👇",
                "viral_formula": "Shock Fact & Apex Predator",
                "predicted_retention": "175% - 185%",
                "why_viral": "Đã chứng minh đạt 1,178 views trên kênh Odisseia Selvagem",
                "fixes_flaw": "Nhân bản chủ đề đại bàng Harpia đã thắng lớn sang bản tiếng Anh.",
                "trend_alignment": "Giải phẫu vũ khí săn mồi hạng nặng của tự nhiên."
            },
            {
                "topic": "Komodo Dragon vs Water Buffalo: How One Venomous Bite Takes Down A 1,200lb Giant",
                "title": "How One Bite From A Komodo Dragon Drops A 1,200lb Giant 🦎💀 #Shorts",
                "hook": "A Water Buffalo outweighs a Komodo Dragon by 1,000 pounds... yet one single bite seals its fate.",
                "angle": "Serrated iron-coated teeth, anticoagulant venom glands, relentless patient tracking",
                "pinned_comment": "Could any land predator survive a Komodo Dragon bite? 👇",
                "viral_formula": "Adrenaline Surge & Predator Biomechanics",
                "predicted_retention": "165% - 175%",
                "why_viral": "Cuộc đối đầu bất đối xứng giữa Rồng Komodo và Trâu rừng luôn thu hút triệu view",
                "fixes_flaw": "Mở đầu bằng cảnh đớp mồi thực tế tốc độ cao trên YouTube Shorts.",
                "trend_alignment": "Giải phẫu cơ sinh học động vật săn mồi (Predator Biomechanics)."
            }
        ]
    else:  # psychology / default
        return [
            {
                "topic": "Why FBI Interrogators Watch Your Feet Instead Of Your Eyes When You Lie",
                "title": "Why FBI Agents Watch Your FEET When You Lie 🕵️‍♂️🧠 #Shorts",
                "hook": "A trained liar can control their face and voice for hours, but they always forget to control their feet.",
                "angle": "Limbic system freeze response, foot-pointing toward the exit door, Joe Navarro FBI behavioral profiling",
                "pinned_comment": "Have you ever caught someone lying by watching their body language? 👇",
                "viral_formula": "Double Tap & Behavioral Secret",
                "predicted_retention": "165% - 175%",
                "why_viral": "Mẹo đọc vị ngôn ngữ cơ thể của FBI luôn đạt tỷ lệ dừng xem (Stay%) trên 65%",
                "fixes_flaw": "Thoát khỏi chuỗi video 'Silence Trick / 3 Signs Toxic' bị lặp lại, chuyển sang kỹ thuật thẩm vấn FBI cực kỳ trực quan.",
                "trend_alignment": "Tâm lý học hành vi & Ngôn ngữ cơ thể FBI (Behavioral Profiling)."
            },
            {
                "topic": "Why Guilty Suspects Always Repeat Your Question Before Answering In Police Rooms",
                "title": "The 1 Verbal Glitch That Exposes A Liar Instantly 🗣️🚨 #Shorts",
                "hook": "When you ask someone a direct question and they repeat your exact words back to you, their brain is stalling for time.",
                "angle": "Cognitive load in deception, echo-questioning to buy 2 seconds to fabricate an alibi, police interrogation transcript analysis",
                "pinned_comment": "Try this test the next time someone hides the truth from you! 👇",
                "viral_formula": "Debate in Comments & Practical Test",
                "predicted_retention": "160% - 170%",
                "why_viral": "Dấu hiệu ngôn từ cụ thể dễ kiểm chứng ngay trong đời thực",
                "fixes_flaw": "Cung cấp dấu hiệu phát hiện nói dối ngay lập tức trong 3 giây đầu kèm cảnh thẩm vấn thực tế.",
                "trend_alignment": "Giải mã tâm lý tội phạm và phát hiện nói dối (Deception Detection)."
            },
            {
                "topic": "The 1971 Stanford Prison Experiment: How Ordinary Students Turned Ruthless In 6 Days",
                "title": "The Psychology Experiment Cancelled After Just 6 Days 🚨🧠 #Shorts",
                "hook": "In 1971, psychologists locked 24 healthy college students in a fake basement prison — by day six, they had to shut it down in horror.",
                "angle": "Philip Zimbardo's Stanford Prison Experiment, instant power corruption, deindividuation behind mirrored sunglasses",
                "pinned_comment": "Does power corrupt good people, or does it just reveal who they really are? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 175%",
                "why_viral": "Các thí nghiệm tâm lý học bị cấm trong lịch sử luôn có tỷ lệ xem hết video (APV) cao nhất ngách tâm lý",
                "fixes_flaw": "Kể chuyện thực nghiệm có thật với tư liệu hình ảnh lịch sử thay vì lời khuyên chung chung.",
                "trend_alignment": "Thí nghiệm tâm lý học đen tối có thật trong lịch sử (Forbidden Dark Psychology Experiments)."
            }
        ]


# ──────────────────────────────────────────────────────────────────────────────
# 3.5 KIỂM TRA AN TOÀN & TỐI ƯU TRƯỚC KHI XUẤT BẢN (PRE-PUBLISH SAFETY AUDIT)
# ──────────────────────────────────────────────────────────────────────────────

def check_pre_publish_safety(job_id: int, channel_id: str) -> Dict[str, Any]:
    """
    Kiểm tra an toàn & tối ưu toàn diện trước khi đăng Shorts:
    1. Kiểm tra trùng lặp nội dung / tiêu đề với kho video của kênh (Duplicate Check).
    2. Kiểm tra khoảng cách thời gian đăng (Cannibalization Proximity Check):
       - Tránh tự bóp tương tác do đăng quá sát video trước (< 3 tiếng, đặc biệt < 1 tiếng như sự cố 44s ngày 26/09).
    3. Kiểm tra tối ưu hóa Shorts (Hook 3s đầu, độ dài tiêu đề <= 60 ký tự, thẻ #Shorts).
    4. Đề xuất khe giờ vàng an toàn kế tiếp để người dùng cân nhắc.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"has_warnings": False, "is_safe": True, "error": f"Không tìm thấy job #{job_id}"}
        
        job_title = job.title or job.topic or ""
        job_topic = job.topic or ""
        scenes_json = job.scenes_json

    portfolio = get_channel_existing_portfolio(channel_id)
    channel_name = portfolio.get("channel_name", channel_id)
    channel_niche = portfolio.get("channel_niche", "general")
    existing_titles = portfolio.get("existing_titles", [])
    
    # Loại trừ chính tiêu đề của job này nếu có trong existing_titles
    candidate_existing = [t for t in existing_titles if t.strip() and t.strip().lower() != job_title.strip().lower()]

    warnings: List[str] = []
    recommendations: List[str] = []
    is_duplicate = False
    duplicate_match = ""
    duplicate_score = 0.0

    # 1. KIỂM TRA TRÙNG LẶP NỘI DUNG (DUPLICATE CHECK)
    if candidate_existing:
        is_dup_t, score_t, match_t = check_idea_similarity(job_title, candidate_existing, similarity_threshold=0.38)
        is_dup_p, score_p, match_p = check_idea_similarity(job_topic, candidate_existing, similarity_threshold=0.38)
        if is_dup_t or is_dup_p:
            is_duplicate = True
            duplicate_score = max(score_t, score_p)
            duplicate_match = match_t or match_p
            warnings.append(
                f"**Trùng lặp nội dung:** Tiêu đề có độ tương đồng {int(duplicate_score * 100)}% với video cũ: *\"{duplicate_match}\"* (dễ bị thuật toán đánh giá là lặp lại/spam đề tài)."
            )
            recommendations.append("Nên đổi góc tiếp cận hoặc thay đổi tiêu đề để tạo sự mới lạ 100%.")

    # 2. KIỂM TRA KHOẢNG CÁCH THỜI GIAN ĐĂNG (CANNIBALIZATION CHECK)
    hours_since_last = portfolio.get("hours_since_last_video", 999.0)
    slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(channel_id)

    if hours_since_last < 3.0:
        if hours_since_last < 1.0:
            mins_ago = max(1, int(hours_since_last * 60))
            warnings.append(
                f"🚨 **Nguy cơ Tự bóp tương tác (Cannibalization):** Kênh vừa xuất bản video cách đây chỉ **{mins_ago} phút**! "
                f"Đăng liền lúc 2 video (tương tự sự cố 44 giây ngày 26/09) sẽ khiến thuật toán Shorts Feed chia đôi lượt phân phối, dẫn tới cả 2 video đều bị flop (dưới 10 views)."
            )
        else:
            warnings.append(
                f"⚠️ **Khoảng cách đăng quá sát:** Kênh vừa có video mới cách đây **{hours_since_last:.1f} giờ**. "
                f"Nên giãn cách tối thiểu 4-12 giờ để video trước tích luỹ đủ lượt xem ban đầu trên Shorts Feed."
            )
        recommendations.append(f"Dời lịch đăng sang khung giờ vàng kế tiếp: **{vn_label}** (*{us_label}*) để tối ưu hóa lượt phân phối.")

    # 3. KIỂM TRA TỐI ƯU TIÊU ĐỀ & HOOK 3S ĐẦU (OPTIMIZATION CHECK)
    clean_title_no_shorts = re.sub(r"#\w+", "", job_title).strip()
    if len(clean_title_no_shorts) > 60:
        warnings.append(
            f"⚠️ **Tiêu đề quá dài ({len(clean_title_no_shorts)} ký tự):** Trên ứng dụng YouTube Shorts di động, tiêu đề dài hơn 60 ký tự sẽ bị cắt bớt (...), làm giảm tỷ lệ người xem tò mò bấm vào."
        )
        recommendations.append("Rút ngắn tiêu đề dưới 60 ký tự, đưa từ khóa kích thích tò mò lên đầu.")

    first_scene_text = ""
    if scenes_json:
        try:
            s_data = json.loads(scenes_json)
            scenes_list = s_data.get("scenes") or (s_data if isinstance(s_data, list) else [])
            if scenes_list and isinstance(scenes_list[0], dict):
                first_scene_text = scenes_list[0].get("voiceover") or scenes_list[0].get("text") or ""
        except Exception:
            pass

    if first_scene_text:
        words = first_scene_text.strip().split()
        if len(words) > 18:
            warnings.append(
                f"⚠️ **Cảnh báo Hook 3s đầu quá dài ({len(words)} từ):** Câu mở đầu dài dòng có nguy cơ làm tụt tỷ lệ Ở lại xem (Stayed to watch) dưới 30% như các video trước."
            )
            recommendations.append("Tối ưu câu hook 3s đầu ngắn gọn (5-10 từ), giật gân, đánh thẳng vào sự tò mò.")

    has_warnings = len(warnings) > 0
    return {
        "has_warnings": has_warnings,
        "is_safe": not has_warnings,
        "job_id": job_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_niche": channel_niche,
        "title": job_title,
        "hours_since_last_video": hours_since_last,
        "warnings": warnings,
        "recommendations": recommendations,
        "next_slot_vn": vn_label,
        "next_slot_us": us_label,
        "next_slot_dt": slot_dt,
        "next_slot_iso": slot_iso
    }


# ──────────────────────────────────────────────────────────────────────────────
# 4. HỆ THỐNG THEO DÕI KPI & BÁO CÁO CẢNH BÁO (CHANNEL KPI MONITOR)
# ──────────────────────────────────────────────────────────────────────────────

def calculate_channel_kpi(channel_id: str) -> Dict[str, Any]:
    """
    Tính toán chỉ số KPI sức khỏe của kênh:
    - Trạng thái: ON_TRACK (< 24h), WARNING (24-48h), OVERDUE_CRITICAL (>= 48h / 2 ngày trễ hạn).
    - Kiểm tra xem có video nào đang chờ duyệt (AWAITING_APPROVAL) hoặc đang render không.
    """
    portfolio = get_channel_existing_portfolio(channel_id)
    hours = portfolio["hours_since_last_video"]
    days = portfolio["days_since_last_video"]

    # Xác định trạng thái KPI
    if hours < KPI_WARNING_HOURS:
        kpi_status = "ON_TRACK"
        status_label = "✅ Đúng Chỉ Tiêu (Tương Tác Cao)"
        color = "green"
        urgency = "normal"
    elif hours < KPI_CRITICAL_OVERDUE_HOURS:
        kpi_status = "WARNING"
        status_label = "⚠️ Sắp Trễ Hạn (Cần Lên Video Mới)"
        color = "yellow"
        urgency = "medium"
    else:
        kpi_status = "OVERDUE_CRITICAL"
        status_label = "🚨 TRỄ DEADLINE (Quá 2 Ngày — Tụt Tương Tác!)"
        color = "red"
        urgency = "critical"

    # Kiểm tra xem kênh có job nào đang trong luồng sản xuất hoặc chờ duyệt không (chỉ tính trong 3 giờ gần nhất)
    pending_job = None
    recent_cutoff = datetime.utcnow() - timedelta(hours=3)
    with get_db() as db:
        job = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.status.in_(["RENDERING", "SCRIPT_READY", "RENDERED", "AWAITING_APPROVAL"]),
            VideoJob.created_at >= recent_cutoff,
        ).order_by(VideoJob.created_at.desc()).first()

        if job:
            pending_job = {
                "job_id": job.id,
                "status": job.status,
                "title": job.title or job.topic,
                "is_auto_generated": bool(job.is_auto_generated),
                "approval_deadline": job.approval_deadline.isoformat() if job.approval_deadline else None,
                "video_path": job.video_path
            }

    return {
        "channel_id": channel_id,
        "channel_name": portfolio["channel_name"],
        "niche": portfolio["channel_niche"],
        "language": portfolio["channel_lang"],
        "handle_or_url": portfolio["handle_or_url"],
        "total_existing_videos": portfolio["total_existing_count"],
        "hours_since_last_video": hours,
        "days_since_last_video": days,
        "last_activity_time": portfolio["last_activity_time"].isoformat() if portfolio["last_activity_time"] else None,
        "kpi_status": kpi_status,
        "status_label": status_label,
        "status_color": color,
        "urgency": urgency,
        "is_overdue_2_days": hours >= KPI_CRITICAL_OVERDUE_HOURS,
        "pending_job": pending_job
    }


def run_network_kpi_audit() -> Dict[str, Any]:
    """
    Kiểm tra toàn bộ mạng lưới kênh YouTube:
    - Tổng hợp danh sách kênh đúng tiến độ, cảnh báo, trễ deadline 2 ngày.
    """
    channels = settings.get_channels_config()
    channels_kpi = []
    overdue_count = 0
    warning_count = 0
    on_track_count = 0

    for ch in channels:
        cid = ch.get("id")
        if not cid:
            continue
        kpi = calculate_channel_kpi(cid)
        channels_kpi.append(kpi)
        if kpi["kpi_status"] == "OVERDUE_CRITICAL":
            overdue_count += 1
        elif kpi["kpi_status"] == "WARNING":
            warning_count += 1
        else:
            on_track_count += 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "total_channels": len(channels_kpi),
        "on_track_count": on_track_count,
        "warning_count": warning_count,
        "overdue_count": overdue_count,
        "channels": channels_kpi
    }


# ──────────────────────────────────────────────────────────────────────────────
# 5. GỬI THÔNG BÁO VÀ NHẮC NHỞ TELEGRAM (TELEGRAM ALERT DISPATCHER)
# ──────────────────────────────────────────────────────────────────────────────

def send_telegram_broadcast(
    text: str,
    inline_keyboard: Optional[List[List[Dict[str, str]]]] = None,
    target_chat_ids: Optional[List[Union[int, str]]] = None,
    prefer_groups: bool = True
):
    """
    Gửi tin nhắn thông báo/nhắc nhở/báo cáo đến Telegram.
    ƯU TIÊN TUYỆT ĐỐI gửi vào Nhóm Telegram (ALLOWED_TELEGRAM_GROUPS) để cả nhóm cùng theo dõi và phối hợp.
    Chỉ khi không có bất kỳ nhóm nào được cấu hình mới fallback gửi về tin nhắn riêng (DM) cho Admin/Creator.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.debug("[TelegramAlert] Không có TELEGRAM_BOT_TOKEN, bỏ qua gửi tin.")
        return

    # Lấy danh sách ID phòng chat nhận tin
    chat_ids = set()
    if target_chat_ids:
        for cid in target_chat_ids:
            chat_ids.add(cid)
    else:
        # 1. Ưu tiên gửi vào các nhóm chat Telegram được cấp phép
        allowed_groups = settings.get_allowed_group_ids()
        if prefer_groups and allowed_groups:
            for gid in allowed_groups:
                chat_ids.add(gid)
        else:
            # 2. Fallback nếu chưa có nhóm: gửi cho Admin/Creator
            for uid in settings.get_allowed_users().keys():
                chat_ids.add(uid)
            try:
                with get_db() as db:
                    db_users = db.query(User).filter(User.telegram_id != 0).all()
                    for u in db_users:
                        chat_ids.add(u.telegram_id)
            except Exception:
                pass

    if not chat_ids:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload_base: Dict[str, Any] = {
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    if inline_keyboard:
        payload_base["reply_markup"] = {"inline_keyboard": inline_keyboard}

    for cid in chat_ids:
        try:
            p = dict(payload_base)
            p["chat_id"] = cid
            res = http_requests.post(url, json=p, timeout=8)
            if res.status_code != 200:
                # Thử fallback plain text nếu lỗi markdown
                p["text"] = re.sub(r"[*_`\[\]()<>]", "", text)
                p.pop("parse_mode", None)
                http_requests.post(url, json=p, timeout=8)
        except Exception as ex:
            logger.debug(f"[TelegramAlert] Lỗi gửi tin cho {cid}: {ex}")


def send_telegram_video_broadcast(
    video_path: str,
    caption: str = "",
    inline_keyboard: Optional[List[List[Dict[str, str]]]] = None,
    target_chat_ids: Optional[List[Union[int, str]]] = None,
    prefer_groups: bool = True
) -> bool:
    """
    Gửi trực tiếp file video hoàn chỉnh (MP4) vào Telegram Group để người dùng xem thử.
    Nếu file > 49MB, tự động nén nhanh trong vài giây bằng FFmpeg CRF 28 để đảm bảo dưới hạn mức 50MB của Telegram!
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not video_path or not Path(video_path).exists():
        logger.debug(f"[TelegramVideoAlert] Không có token hoặc không tìm thấy file video: {video_path}")
        return False

    chat_ids = set()
    if target_chat_ids:
        for cid in target_chat_ids:
            chat_ids.add(cid)
    else:
        allowed_groups = settings.get_allowed_group_ids()
        if prefer_groups and allowed_groups:
            for gid in allowed_groups:
                chat_ids.add(gid)
        else:
            for uid in settings.get_allowed_users().keys():
                chat_ids.add(uid)

    if not chat_ids:
        return False

    # Kiểm tra kích thước file; nếu > 49MB nén nhanh bằng FFmpeg
    upload_path = video_path
    temp_compressed = None
    try:
        size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        if size_mb > 49.0:
            import subprocess
            temp_dir = Path(settings.TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_compressed = str(temp_dir / f"tg_preview_{Path(video_path).stem}.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                "-c:a", "copy", temp_compressed
            ]
            logger.info(f"[TelegramVideoAlert] File {size_mb:.1f}MB > 49MB, đang nén nhanh video cho Telegram...")
            res_comp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if res_comp.returncode == 0 and Path(temp_compressed).exists():
                upload_path = temp_compressed
                logger.info(f"[TelegramVideoAlert] Đã nén video thành công: {Path(upload_path).stat().st_size / (1024*1024):.1f}MB")
    except Exception as ex_comp:
        logger.warning(f"[TelegramVideoAlert] Lỗi khi nén video: {ex_comp}")

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    cached_file_id = None
    sent_any = False

    for cid in chat_ids:
        try:
            data = {
                "chat_id": cid,
                "caption": caption[:1000] if caption else "",
                "parse_mode": "HTML",
                "supports_streaming": True
            }
            if inline_keyboard:
                data["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})

            if cached_file_id:
                # Chat thứ 2 trở đi gửi qua file_id chỉ mất 0.2s!
                data["video"] = cached_file_id
                r = http_requests.post(url, data=data, timeout=15)
                if r.status_code == 200:
                    sent_any = True
            else:
                with open(upload_path, "rb") as vf:
                    r = http_requests.post(url, data=data, files={"video": vf}, timeout=180)
                    if r.status_code == 200:
                        sent_any = True
                        res_json = r.json()
                        v_obj = res_json.get("result", {}).get("video")
                        if v_obj and v_obj.get("file_id"):
                            cached_file_id = v_obj.get("file_id")
                    elif r.status_code != 200:
                        # Thử lại không parse_mode nếu có lỗi HTML tags
                        data.pop("parse_mode", None)
                        with open(upload_path, "rb") as vf2:
                            r2 = http_requests.post(url, data=data, files={"video": vf2}, timeout=180)
                            if r2.status_code == 200:
                                sent_any = True
                                res_json2 = r2.json()
                                v_obj2 = res_json2.get("result", {}).get("video")
                                if v_obj2 and v_obj2.get("file_id"):
                                    cached_file_id = v_obj2.get("file_id")
        except Exception as ex_send:
            logger.debug(f"[TelegramVideoAlert] Lỗi gửi video cho {cid}: {ex_send}")

    if temp_compressed and Path(temp_compressed).exists():
        try:
            os.remove(temp_compressed)
        except Exception:
            pass

    return sent_any


# ──────────────────────────────────────────────────────────────────────────────
# 6. TỰ ĐỘNG SẢN XUẤT VIDEO CHO KÊNH TRỄ 2 NGÀY (AUTO-GENERATION ENGINE)
# ──────────────────────────────────────────────────────────────────────────────

def trigger_auto_generation_for_overdue_channel(
    channel_id: str,
    grace_hours: float = DEFAULT_APPROVAL_GRACE_HOURS,
    force: bool = False
) -> Dict[str, Any]:
    """
    Kích hoạt tự động sản xuất video cứu tương tác khi một kênh bị trễ deadline >= 2 ngày (hoặc force=True):
    1. Kiểm tra xem có video nào đang xử lý dở không (tránh sản xuất thừa lãng phí tài nguyên).
    2. Chọn 1 ý tưởng độc lạ tốt nhất từ suggest_channel_topics (chống trùng lặp 100%).
    3. Tự động sinh kịch bản chi tiết qua ContentAgent.
    4. Tạo VideoJob trong SQLite và kích hoạt Render Pipeline trong background thread.
    5. Thiết lập thời hạn duyệt (approval_deadline = now + grace_hours).
    6. Gửi cảnh báo thúc giục người dùng kiểm tra trên Telegram và Web Dashboard!
    """
    kpi = calculate_channel_kpi(channel_id)
    if not force and not getattr(settings, "ENABLE_OVERDUE_AUTO_PRODUCTION", False):
        logger.info(f"[AutoGeneration] Kênh '{channel_id}' trễ hạn nhưng ENABLE_OVERDUE_AUTO_PRODUCTION đang tắt (False). Không tự ý sinh video.")
        return {
            "success": False,
            "message": f"Tính năng tự động sản xuất video quá hạn đang tắt (ENABLE_OVERDUE_AUTO_PRODUCTION=false). Hãy chủ động gõ lệnh tạo video.",
            "kpi": kpi
        }

    if not kpi["is_overdue_2_days"] and not force:
        return {
            "success": False,
            "message": f"Kênh '{kpi['channel_name']}' chưa trễ hạn 2 ngày (mới trễ {kpi['days_since_last_video']} ngày). Dùng force=True nếu muốn ép buộc tạo ngay.",
            "kpi": kpi
        }

    # Tránh spam: nếu đã có job chờ duyệt hoặc đang render, không tạo thêm
    if kpi.get("pending_job"):
        pj = kpi["pending_job"]
        return {
            "success": False,
            "message": f"Kênh đã có video đang trong hàng đợi xử lý (#{pj['job_id']} - {pj['status']}).",
            "job_id": pj["job_id"],
            "status": pj["status"]
        }

    logger.info(f"[AutoGeneration] 🚨 Kênh '{kpi['channel_name']}' ({channel_id}) trễ {kpi['days_since_last_video']} ngày! Khởi động AI Auto-Production...")

    # 1. Gợi ý ý tưởng không trùng lặp
    topics = suggest_channel_topics(channel_id, count=1)
    if not topics:
        return {"success": False, "error": "Không thể sinh ý tưởng mới cho kênh này."}

    best_topic = topics[0]
    topic_str = best_topic.get("topic", "High Retention Shorts")
    title_str = best_topic.get("title", f"Video_{datetime.utcnow().strftime('%Y%m%d_%H%M')}")
    niche_id = kpi["niche"]
    lang = kpi["language"]

    # 2. Sinh kịch bản phân cảnh chuẩn viral với Entity Cooldown Tracker
    from app.services.entity_tracker import get_channel_cooldown_entities, register_job_entities
    cooldown_ents = get_channel_cooldown_entities(channel_id, limit_jobs=15)

    agent = ContentAgent()
    try:
        script = agent.generate_script(
            topic=topic_str,
            channel_niche=niche_id,
            language=lang,
            director_notes="Kịch bản kích hoạt tự động cứu tương tác kênh. Chuẩn 5-Act 32-42s Golden Depth (88-118 từ, 8-9 cảnh trọn ý): mở đầu khẳng định sắc lẹm, có ví dụ câu nói thật + cơ chế não bộ + chiêu phản đòn cụ thể, kết thúc Seamless Loop nối về Cảnh 1 (tuyệt đối không đọc CTA Subscribe).",
            cooldown_entities=cooldown_ents
        )
    except Exception as e:
        logger.error(f"[AutoGeneration] Lỗi sinh kịch bản cho {channel_id}: {e}")
        return {"success": False, "error": f"Lỗi sinh kịch bản: {e}"}

    # 3. Tạo VideoJob trong Database
    now_utc = datetime.utcnow()
    deadline = now_utc + timedelta(hours=grace_hours)

    with get_db() as db:
        job = VideoJob(
            user_id=0,  # 0 = System Auto-Generator
            target_channel_id=channel_id,
            topic=topic_str,
            title=script.title or title_str,
            script=script.full_narration,
            description=script.description,
            tags=",".join(script.tags),
            scenes_json=script.to_json_str(),
            status="SCRIPT_READY",
            is_auto_generated=1,
            approval_deadline=deadline,
            progress_percent=0,
            progress_message="Hệ thống tự động lên lịch dựng video cứu tương tác..."
        )
        db.add(job)
        db.flush()
        job_id = job.id

        log_activity(
            db,
            telegram_id=0,
            username="AI_Guardian",
            action_type="AUTO_GENERATE_OVERDUE",
            details=f"Tự động sản xuất video cứu tương tác cho kênh '{kpi['channel_name']}' (Job #{job_id}): '{job.title}'",
            channel_id=channel_id
        )

        # Ghi nhận thực thể vào Cooldown Memory
        try:
            register_job_entities(channel_id, script, job_id=job_id)
        except Exception as e_reg:
            logger.debug(f"Không thể ghi nhận thực thể cooldown: {e_reg}")

    # 4. Gửi thông báo Telegram để duyệt và render trực tiếp bằng Local Hardware Engine
    def _background_render_worker(jid: int, ch_name: str, ch_id: str, deadl: datetime):
        try:
            with get_db() as _db:
                _j = _db.query(VideoJob).filter(VideoJob.id == jid).first()
                video_title = _j.title if _j else "Video Shorts"

            grace_str = f"{grace_hours:.0f}"
            alert_text = (
                f"🚨 **CẢNH BÁO TỤT TƯƠNG TÁC KÊNH: `{ch_name}`**\n\n"
                f"⚠️ Kênh này đã trễ lịch đăng bài!\n"
                f"🤖 **AI Guardian đã tự động tạo kịch bản sẵn sàng:**\n"
                f"🎬 **Tiêu đề:** *{video_title}*\n"
                f"⏱️ **Hạn chót duyệt:** `{deadl.strftime('%H:%M %d/%m/2026')}` (còn ~{grace_str} tiếng)\n\n"
                f"👉 Bấm nút bên dưới để duyệt và dựng video ngay:"
            )
            inline_kb = [
                [
                    {"text": f"🎬 Duyệt & Dựng Ngay (#{jid})", "callback_data": f"render_video:{jid}"},
                    {"text": f"❌ Huỷ bỏ (#{jid})", "callback_data": f"approve_auto:{jid}:cancel"}
                ]
            ]
            send_telegram_broadcast(alert_text, inline_keyboard=inline_kb)
        except Exception as ex:
            logger.error(f"[AutoGeneration] Lỗi gửi thông báo #{jid}: {ex}", exc_info=True)

    render_thread = threading.Thread(
        target=_background_render_worker,
        args=(job_id, kpi["channel_name"], channel_id, deadline),
        daemon=True
    )
    render_thread.start()

    return {
        "success": True,
        "message": f"Đã tự động khởi tạo và đưa vào hàng đợi dựng video #{job_id} cho kênh '{kpi['channel_name']}'",
        "job_id": job_id,
        "topic": topic_str,
        "title": script.title,
        "approval_deadline": deadline.isoformat(),
        "grace_hours": grace_hours
    }


# ──────────────────────────────────────────────────────────────────────────────
# 7. CƠ CHẾ ĐĂNG TẠM KHẨN CẤP (FALLBACK GRACE-PERIOD PUBLISHER)
# ──────────────────────────────────────────────────────────────────────────────

def process_fallback_publishing() -> List[Dict[str, Any]]:
    """
    Quét tất cả các video tự động sinh (is_auto_generated=1):
    - Đang ở trạng thái AWAITING_APPROVAL.
    - Đã vượt quá thời hạn chờ duyệt (approval_deadline <= now).
    - Người dùng bận không xác nhận.
    => Tự động lên lịch hoặc đăng tạm lên YouTube để duy trì nhịp tương tác thuật toán (nếu ENABLE_FALLBACK_AUTO_PUBLISH=true)!
    """
    if not getattr(settings, "ENABLE_FALLBACK_AUTO_PUBLISH", False):
        return []

    now_utc = datetime.utcnow()
    published_jobs = []

    with get_db() as db:
        expired_jobs = db.query(VideoJob).filter(
            VideoJob.is_auto_generated == 1,
            VideoJob.status == "AWAITING_APPROVAL",
            VideoJob.approval_deadline <= now_utc,
            VideoJob.video_path.isnot(None)
        ).all()

        for job in expired_jobs:
            # Kiểm tra file video có tồn tại
            if not Path(job.video_path).exists():
                logger.warning(f"[FallbackPublish] File video Job #{job.id} không tồn tại trên ổ đĩa!")
                continue

            target_ch = job.target_channel_id
            job_id = job.id
            job_title = job.title or job.topic

            logger.info(f"[FallbackPublish] ⏰ Video #{job_id} ('{job_title}') hết hạn duyệt ({job.approval_deadline}). Tiến hành tự động đăng tạm cho kênh '{target_ch}'...")

            # Tính toán khung giờ vàng gần nhất
            slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)

            job.status = "SCHEDULED"
            job.scheduled_time = slot_dt
            job.progress_message = f"Đã tự động đăng tạm (Lên lịch công chiếu lúc {vn_label} để duy trì tương tác)"

            log_activity(
                db,
                telegram_id=0,
                username="AI_Guardian",
                action_type="FALLBACK_AUTO_PUBLISH",
                details=f"Tự động đăng tạm Video #{job_id} '{job_title}' lên lịch công chiếu lúc {vn_label} ({us_label}) do người dùng chưa duyệt sau thời hạn",
                channel_id=target_ch
            )

            published_jobs.append({
                "job_id": job_id,
                "title": job_title,
                "channel_id": target_ch,
                "scheduled_slot_vn": vn_label,
                "scheduled_slot_us": us_label
            })

            # Thông báo Telegram
            notify_text = (
                f"⏰ **TỰ ĐỘNG ĐĂNG TẠM THÀNH CÔNG (GIỮ NHỊP TƯƠNG TÁC)**\n\n"
                f"📺 **Kênh:** `{target_ch}`\n"
                f"🎬 **Video #{job_id}:** *{job_title}*\n"
                f"⌛ **Lý do:** Đã qua thời hạn duyệt nhưng bạn đang bận. Hệ thống đã kích hoạt cơ chế bảo vệ kênh.\n"
                f"📅 **Lịch công chiếu:** `{vn_label}` (Khung giờ vàng chuẩn quốc tế: `{us_label}`)\n\n"
                f"✅ Thuật toán phân phối Shorts Feed của kênh tiếp tục được duy trì ổn định!"
            )
            send_telegram_broadcast(notify_text)

    return published_jobs


# ──────────────────────────────────────────────────────────────────────────────
# 8. TIẾN TRÌNH CHẠY NGẦM ĐỊNH KỲ (CHANNEL INTELLIGENCE BACKGROUND WORKER)
# ──────────────────────────────────────────────────────────────────────────────

class ChannelIntelligenceWorker:
    """Worker chạy ngầm định kỳ (mỗi 30 phút):
    - Kiểm tra fallback publish cho các video hết hạn duyệt.
    - Kiểm tra KPI sức khỏe các kênh.
    - Tự động kích hoạt sản xuất video cho các kênh trễ deadline >= 2 ngày.
    """
    _instance = None
    _thread = None
    _running = False

    @classmethod
    def start(cls, check_interval_minutes: int = 30):
        if cls._running:
            return
        cls._running = True

        def _loop():
            logger.info(f"🚀 [ChannelIntelligence] Background Worker đã khởi động (chu kỳ kiểm tra: {check_interval_minutes} phút)!")
            while cls._running:
                try:
                    # 1. Xử lý đăng tạm cho các video hết hạn duyệt (nếu người dùng bật)
                    if getattr(settings, "ENABLE_FALLBACK_AUTO_PUBLISH", False):
                        process_fallback_publishing()

                    # 2. Quét KPI toàn mạng lưới kênh
                    audit = run_network_kpi_audit()
                    overdue_channels = [c for c in audit.get("channels", []) if c.get("kpi_status") == "OVERDUE_CRITICAL"]

                    if getattr(settings, "ENABLE_OVERDUE_AUTO_PRODUCTION", False):
                        for ch in overdue_channels:
                            cid = ch["channel_id"]
                            try:
                                # Nếu kênh chưa có job đang chạy, tự động kích hoạt
                                if not ch.get("pending_job"):
                                    logger.info(f"[ChannelIntelligence] Phát hiện kênh '{cid}' trễ deadline 2 ngày! Tự động sản xuất video cứu nguy...")
                                    trigger_auto_generation_for_overdue_channel(cid)
                            except Exception as e_ch:
                                logger.error(f"[ChannelIntelligence] Lỗi tự động sản xuất cho kênh '{cid}': {e_ch}")
                    else:
                        if overdue_channels:
                            logger.info(f"[ChannelIntelligence] Có {len(overdue_channels)} kênh trễ hạn nhưng ENABLE_OVERDUE_AUTO_PRODUCTION đang tắt (False). Không tự sinh video.")


                except Exception as ex:
                    logger.error(f"[ChannelIntelligence] Lỗi chu kỳ worker: {ex}")

                # Chờ đến chu kỳ tiếp theo
                import time
                for _ in range(check_interval_minutes * 6):
                    if not cls._running:
                        break
                    time.sleep(10)

        cls._thread = threading.Thread(target=_loop, daemon=True, name="ChannelIntelligenceWorker")
        cls._thread.start()

    @classmethod
    def stop(cls):
        cls._running = False
