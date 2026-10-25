"""
app.services.intelligence.comment_intelligence — Bộ Đào Bình Luận Khán Giả & Lọc Cục Bộ 0-Token (Comment Intelligence & Zero-Token Filter).

Tích hợp nguyên lý từ GOHA YT Engage (Video Content Intelligence & Local Heuristic Pre-Filter):
1. Zero-Token Local Heuristic Classifier (`classify_comment_zero_token`):
   - Phân loại bình luận ngay trên máy với chi phí 0 Token thành 6 nhóm:
     `praise_or_emoji`, `spam_or_noise`, `topic_request`, `metric_or_fact_correction`, `open_question`, `audience_pain_or_debate`.
   - Loại bỏ các bình luận xã giao/khen ngợi đơn giản hoặc chỉ có emoji trước khi gọi LLM, tiết kiệm 60–80% token.
2. Video Comment Intelligence Miner (`mine_channel_comment_intelligence`):
   - Thu thập và tổng hợp 4 mỏ vàng dữ liệu từ bình luận thật của khán giả:
     * `audiencePains`: Nỗi đau / trải nghiệm thực tế người xem chia sẻ.
     * `openQuestions`: Câu hỏi lặp đi lặp lại chưa được giải đáp.
     * `topicRequests`: Chủ đề / kèo đối đầu `A vs B` khán giả yêu cầu làm tiếp.
     * `hotspots`: Điểm nóng tranh luận số liệu/khoa học dưới phần bình luận (dùng làm Hook phản biện).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Đường dẫn bộ nhớ đệm chống trùng lặp bình luận và lưu trữ báo cáo Radar Intelligence
_COMMENT_INTEL_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "comment_intelligence_cache.json"

_TOPIC_REQUEST_PATTERN = re.compile(
    r"\b(?:please make|do a video|make a video|next video|can you do|what about|compare|"
    r"làm về|làm thêm về|so sánh|kèo tiếp theo|hãy làm|"
    r"faz sobre|fala sobre|faz um vídeo|próximo vídeo|compara)\b",
    re.IGNORECASE,
)

_QUESTION_PATTERN = re.compile(
    r"(?:\?|\b(?:why|how|what if|is it true|could|would|who|where|when|"
    r"tại sao|vì sao|làm sao|như thế nào|liệu có|"
    r"por que|como|será que|qual|onde)\b)",
    re.IGNORECASE,
)

_METRIC_CORRECTION_PATTERN = re.compile(
    r"(?:\b\d+(?:[.,]\d+)?\s*(?:psi|m|km|mph|km/h|kg|lbs|tons|db|hz|°c|°f|%|years|seconds|hours)\b|"
    r"\b(?:actually|fact check|wrong|incorrect|myth|real reason|thực ra|sai rồi|chính xác là|na verdade|errado)\b)",
    re.IGNORECASE,
)

_SIMPLE_PRAISE_PATTERN = re.compile(
    r"^(?:great|awesome|nice|cool|good|love it|amazing|first|wow|thanks|thank you|"
    r"hay|hay quá|tuyệt vời|cảm ơn|đỉnh|quá đỉnh|"
    r"muito bom|incrível|top|perfeito|amei|parabéns|obrigado)[\s!.,❤️🔥👍👏💯🙌✨]*$",
    re.IGNORECASE,
)

_SPAM_PATTERN = re.compile(
    r"(?:https?://|www\.|t\.me/|whatsapp|crypto|forex|sub4sub|check my channel)",
    re.IGNORECASE,
)


def classify_comment_zero_token(comment_text: str) -> Dict[str, Any]:
    """
    Phân loại bình luận ngay tại máy với chi phí 0 Token (Zero-Token Heuristic Pre-Filter).
    Câu hỏi đầu tiên không phải 'dùng model nào' mà là 'bình luận này có cần gọi model không?'.
    """
    raw = str(comment_text or "").strip()
    if not raw or len(raw) < 3:
        return {
            "category": "spam_or_noise",
            "signal_bucket": None,
            "needs_llm": False,
            "reason": "Empty or too short",
        }

    if _SPAM_PATTERN.search(raw):
        return {
            "category": "spam_or_noise",
            "signal_bucket": None,
            "needs_llm": False,
            "reason": "External link or promotional spam",
        }

    # Loại bỏ toàn bộ ký tự không phải chữ/số để kiểm tra comment chỉ toàn emoji
    alpha_num = re.sub(r"[^\w]", "", raw, flags=re.UNICODE)
    if len(alpha_num) < 3 or _SIMPLE_PRAISE_PATTERN.match(raw):
        return {
            "category": "praise_or_emoji",
            "signal_bucket": None,
            "needs_llm": False,
            "reason": "Simple praise, thanks, or emoji-only reaction (0-token skip)",
        }

    # Kiểm tra yêu cầu chủ đề mới (Topic Requests)
    if _TOPIC_REQUEST_PATTERN.search(raw):
        return {
            "category": "topic_request",
            "signal_bucket": "topicRequests",
            "needs_llm": True,
            "reason": "Viewer explicitly requesting a topic or matchup",
        }

    # Kiểm tra phản biện số liệu / tranh luận nóng (Hotspots)
    if _METRIC_CORRECTION_PATTERN.search(raw) and len(raw.split()) >= 5:
        return {
            "category": "metric_or_fact_correction",
            "signal_bucket": "hotspots",
            "needs_llm": True,
            "reason": "Contains physical metric or factual counterpoint debate",
        }

    # Kiểm tra câu hỏi mở của khán giả (Open Questions)
    if _QUESTION_PATTERN.search(raw) and len(raw.split()) >= 4:
        return {
            "category": "open_question",
            "signal_bucket": "openQuestions",
            "needs_llm": True,
            "reason": "Substantive viewer question",
        }

    # Các bình luận dài chia sẻ trải nghiệm / nỗi đau / góc nhìn (Audience Pains)
    if len(raw.split()) >= 7:
        return {
            "category": "audience_pain_or_debate",
            "signal_bucket": "audiencePains",
            "needs_llm": True,
            "reason": "Detailed viewer experience or psychological pain point",
        }

    return {
        "category": "praise_or_emoji",
        "signal_bucket": None,
        "needs_llm": False,
        "reason": "Short generic remark",
    }


def aggregate_comments_into_intelligence(
    comments: List[Dict[str, Any]],
    video_title: str = "",
) -> Dict[str, Any]:
    """
    Gom nhóm danh sách bình luận (`[{"id": ..., "text": ..., "likes": ...}]`) qua bộ lọc 0-Token
    và trả về cấu trúc 4 mảng dữ liệu cốt lõi (`audiencePains`, `openQuestions`, `topicRequests`, `hotspots`)
    kèm thống kê tỷ lệ tiết kiệm Token.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "audiencePains": [],
        "openQuestions": [],
        "topicRequests": [],
        "hotspots": [],
    }
    seen_ids = set()
    total_count = 0
    zero_token_skipped = 0

    for item in comments or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or item.get("comment_id") or "").strip()
        text = str(item.get("text") or item.get("textDisplay") or "").strip()
        if not text:
            continue
        dedup_key = cid or text.lower()[:80]
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)
        total_count += 1

        cls_res = classify_comment_zero_token(text)
        if not cls_res["needs_llm"] or not cls_res["signal_bucket"]:
            zero_token_skipped += 1
            continue

        bucket_name = cls_res["signal_bucket"]
        likes = int(item.get("likes") or item.get("likeCount") or 0)
        buckets[bucket_name].append({
            "id": cid,
            "text": text[:240],
            "likes": likes,
            "category": cls_res["category"],
            "video_title": video_title,
        })

    # Sắp xếp ưu tiên bình luận nhiều Like nhất lên đầu
    for k in buckets:
        buckets[k].sort(key=lambda x: x.get("likes", 0), reverse=True)
        buckets[k] = buckets[k][:8]

    saved_pct = round((zero_token_skipped / total_count) * 100.0, 1) if total_count > 0 else 0.0
    return {
        "total_comments_scanned": total_count,
        "zero_token_filtered_count": zero_token_skipped,
        "token_savings_pct": saved_pct,
        "audiencePains": buckets["audiencePains"],
        "openQuestions": buckets["openQuestions"],
        "topicRequests": buckets["topicRequests"],
        "hotspots": buckets["hotspots"],
    }


def mine_channel_comment_intelligence(
    channel_id: str,
    max_videos: int = 3,
    max_comments_per_video: int = 20,
    use_live_api: bool = True,
) -> Dict[str, Any]:
    """
    Quét bình luận thật từ các video gần đây của kênh (hoặc đọc từ bộ nhớ đệm `comment_intelligence_cache.json`)
    để tìm câu hỏi bỏ ngỏ, yêu cầu chủ đề và điểm nóng tranh luận.
    """
    raw_comments: List[Dict[str, Any]] = []

    # 1. Đọc từ cache cục bộ trước nếu có
    try:
        if _COMMENT_INTEL_CACHE_PATH.exists():
            cache_data = json.loads(_COMMENT_INTEL_CACHE_PATH.read_text(encoding="utf-8"))
            ch_cached = cache_data.get(channel_id)
            if isinstance(ch_cached, dict) and ch_cached.get("comments"):
                raw_comments.extend(ch_cached["comments"])
    except Exception as e:
        logger.debug(f"[CommentIntel] Không thể đọc cache cho {channel_id}: {e}")

    # 2. Nếu được phép gọi API đọc-chỉ (Read-Only) và kênh đã cấu hình OAuth/Service
    if use_live_api and not raw_comments:
        try:
            from app.agents.publisher_agent import PublisherAgent
            channels_list = settings.get_channels_config()
            matched_cfg = next(
                (c for c in channels_list if c.get("id") == channel_id or c.get("handle") == channel_id),
                None,
            )
            if matched_cfg:
                pub = PublisherAgent(channel_config=matched_cfg)
                if getattr(pub, "service", None):
                    ch_res = pub.service.channels().list(mine=True, part="contentDetails").execute()
                    if ch_res.get("items"):
                        up_id = ch_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
                        pl_res = pub.service.playlistItems().list(
                            playlistId=up_id,
                            part="snippet",
                            maxResults=max(1, min(max_videos, 5)),
                        ).execute()
                        for item in pl_res.get("items", []):
                            vid_id = item.get("snippet", {}).get("resourceId", {}).get("videoId")
                            vid_title = item.get("snippet", {}).get("title", "")
                            if not vid_id:
                                continue
                            try:
                                c_res = pub.service.commentThreads().list(
                                    part="snippet",
                                    videoId=vid_id,
                                    maxResults=max(5, min(max_comments_per_video, 50)),
                                    order="relevance",
                                    textFormat="plainText",
                                ).execute()
                                for c_item in c_res.get("items", []):
                                    top_c = c_item.get("snippet", {}).get("topLevelComment", {})
                                    c_snip = top_c.get("snippet", {})
                                    raw_comments.append({
                                        "id": top_c.get("id", ""),
                                        "text": c_snip.get("textDisplay", ""),
                                        "likes": int(c_snip.get("likeCount", 0) or 0),
                                        "video_title": vid_title,
                                    })
                            except Exception:
                                continue
        except Exception as e_api:
            logger.debug(f"[CommentIntel] Bỏ qua quét live API cho {channel_id}: {e_api}")

    intel = aggregate_comments_into_intelligence(raw_comments)
    intel["channel_id"] = channel_id
    return intel


def build_comment_intelligence_prompt_block(
    channel_id: str,
    niche_id: str = "general",
    precomputed_intel: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Biên dịch kết quả đào bình luận thành khối chỉ dẫn chiến lược (Radar Comment Intelligence Block)
    để nhúng trực tiếp vào `suggest_channel_topics()`.
    """
    intel = precomputed_intel or mine_channel_comment_intelligence(
        channel_id=channel_id,
        use_live_api=False,
    )
    pains = intel.get("audiencePains") or []
    questions = intel.get("openQuestions") or []
    requests = intel.get("topicRequests") or []
    hotspots = intel.get("hotspots") or []

    if not (pains or questions or requests or hotspots):
        return ""

    lines: List[str] = [
        "═══════════════════════════════════════════════════════════",
        "💬 AUDIENCE COMMENT INTELLIGENCE (MINED FROM REAL VIEWER COMMENTS — 0-TOKEN PRE-FILTERED):",
    ]
    if requests:
        lines.append("  • Viewer Topic & Matchup Requests:")
        for r in requests[:3]:
            lines.append(f"    - \"{r['text']}\" ({r.get('likes', 0)} likes)")
    if questions:
        lines.append("  • Unanswered Audience Questions (High Retention Hook Potential):")
        for q in questions[:3]:
            lines.append(f"    - \"{q['text']}\" ({q.get('likes', 0)} likes)")
    if hotspots:
        lines.append("  • Comment Debate Hotspots & Metric Counterpoints:")
        for h in hotspots[:3]:
            lines.append(f"    - \"{h['text']}\" ({h.get('likes', 0)} likes)")
    if pains:
        lines.append("  • Relatable Audience Pain Points:")
        for p in pains[:3]:
            lines.append(f"    - \"{p['text']}\" ({p.get('likes', 0)} likes)")

    lines.append(
        "  ↳ DIRECTIVE: Prioritize at least 1-2 candidate topics that directly answer these viewer questions, "
        "fulfill requested matchups, or settle a heated comment debate!"
    )
    return "\n".join(lines) + "\n"
