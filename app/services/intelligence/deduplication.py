"""app.services.intelligence.deduplication — Thuật toán chống trùng lặp ý tưởng & kiểm tra lệch ngách."""
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
    # Loại bỏ các từ dừng thông dụng trong tiêu đề Shorts (EN / PT-BR / VI)
    stop_words = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "this", "that", "why", "how", "what", "signs", "sign",
        "video", "shorts", "viral", "psychology", "mystery", "history",
        "they", "them", "their", "you", "your", "when", "into", "from",
        "was", "were", "been", "have", "has", "had", "will", "would", "could",
        "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "se",
        "ao", "aos", "as", "os", "um", "uma", "uns", "umas", "ou", "já", "só",
        "há", "até", "pelo", "pela", "pelos", "pelas", "num", "numa", "dele",
        "dela", "este", "esta", "esse", "essa", "isto", "isso", "aquele", "aquela",
        "muito", "muita", "onde", "quando", "quem", "qual", "quais", "sobre",
        "entre", "contra", "sem", "sob", "após", "antes", "depois", "por", "que",
        "para", "com", "mais", "como", "foi", "são", "era", "tem", "têm", "seu", "sua",
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
    existing_items: Optional[List[str]] = None,
    similarity_threshold: float = 0.40,
    channel_id: Optional[str] = None,
) -> Tuple[bool, float, str]:
    """
    Kiểm tra xem ý tưởng/tiêu đề mới có bị trùng lặp hoặc quá giống với danh sách video đã làm không.
    Sử dụng kết hợp:
    1. Jaccard Token Overlap + Substring Keyword Containment trên `existing_items`.
    2. Mini-RAG (`VideoMemoryRAG`) truy vấn ngữ nghĩa BM25/TF-IDF trên toàn bộ kịch bản (`full_script`),
       câu mở đầu (`hook_text`) và metadata hình ảnh (`visual_metadata`) đã lưu của kênh.
    Trả về: (is_duplicate: bool, max_score: float, matched_item: str)
    """
    if not candidate_title_or_topic:
        return False, 0.0, ""

    norm_candidate = _normalize_text_for_similarity(candidate_title_or_topic)
    cand_tokens = set(norm_candidate.split())
    if not cand_tokens:
        return False, 0.0, ""

    max_score = 0.0
    best_match = ""

    for item in (existing_items or []):
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

        # Nếu chỉ trùng đúng 1 từ đơn lẻ (vd: 'oceano', 'metros', 'shark'), không được phép vọt điểm containment ảo
        if len(intersection) == 1 and min(len(cand_tokens), len(item_tokens)) >= 3:
            combined_score = min(0.22, jaccard)
        else:
            combined_score = max(jaccard, containment * 0.75)

        if combined_score > max_score:
            max_score = combined_score
            best_match = item

    # 3. Truy vấn Mini-RAG (nếu có channel_id hoặc tìm trong toàn kho kịch bản + visual metadata)
    if channel_id:
        try:
            from app.services.video_memory_rag import get_video_memory_rag
            rag_hits = get_video_memory_rag().query_similar(
                query_text=candidate_title_or_topic,
                channel_id=channel_id,
                top_k=2,
                min_score=max(0.25, similarity_threshold * 0.85),
            )
            for hit in rag_hits:
                rag_score = float(hit.get("similarity_score", 0.0))
                if rag_score > max_score:
                    max_score = rag_score
                    best_match = hit.get("title") or hit.get("topic") or hit.get("hook_text") or ""
        except Exception as ex_rag:
            logger.debug(f"[AntiDuplication] Mini-RAG similarity check warning: {ex_rag}")

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

    from app.services.intelligence.portfolio_scanner import get_channel_existing_portfolio
    portfolio = get_channel_existing_portfolio(channel_id)
    existing_titles = portfolio.get("existing_titles", [])

    is_dup, score, matched = check_idea_similarity(
        clean_cand,
        existing_titles,
        similarity_threshold=similarity_threshold,
        channel_id=channel_id,
    )
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
