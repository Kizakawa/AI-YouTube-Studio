"""
Bilibili Fetcher & Cross-Border Repurposer Helper (Bilibili -> YouTube Shorts).

Tính năng chính:
1. Tìm kiếm video Bilibili siêu tốc (~0.7s) qua API tìm kiếm trực tiếp với cookie phiên `buvid3`.
2. Tự động dịch câu truy vấn tiếng Anh / Bồ Đào Nha / Việt sang tiếng Trung giản thể (`zh-CN`)
   tối ưu cho các ngách: Bí ẩn biển sâu (深海), Quái vật / Tiền sử (远古巨兽), Lịch sử giả tưởng (架空历史),
   Tâm lý học (心理学), Sinh tồn hoang dã (野生动物 捕食).
3. Trích xuất trực tiếp luồng 1080p/720p H.264 (`avc1`) qua chữ ký WBI của `yt-dlp` (`BiliBiliIE`)
   kèm `try_look=1` mà không cần tài khoản VIP.
4. Cắt khung hình thông minh **Bilibili Safe-Crop (`crop=iw:ih*0.78:0:ih*0.08`)**:
   Loại bỏ vật lý 8% mép trên (nơi chứa logo Bilibili + tên UP chủ) và 14% mép dưới (nơi chứa phụ đề cứng tiếng Trung)
   trước khi chuyển sang khung dọc 9:16 (`1080x1920`) và đưa qua cổng kiểm duyệt OCR/Vision Critic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_BILI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_BILI_HEADERS = {
    "User-Agent": _BILI_UA,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

_BVID_RE = re.compile(r"\b(BV1[0-9A-Za-z]{9})\b")
_AVID_RE = re.compile(r"\bav(\d{4,18})\b", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_zh_query_cache: Dict[str, str] = {}
_bili_search_cache: Dict[str, List[Dict[str, Any]]] = {}
_bili_lock = threading.Lock()

# Từ điển thuật ngữ cốt lõi giúp dịch tức thì (0ms) ngay cả khi không gọi LLM
_CORE_TERM_ZH_MAP: Dict[str, str] = {
    "deep sea": "深海",
    "ocean": "海洋",
    "mariana trench": "马里亚纳海沟",
    "anglerfish": "鮟鱇鱼",
    "giant squid": "大王乌贼",
    "colossal squid": "大王酸浆鱿",
    "megalodon": "巨齿鲨",
    "leviathan": "利维坦鲸",
    "kraken": "北海巨妖",
    "bloop": "深海怪声 Bloop",
    "shark": "鲨鱼",
    "orca": "虎鲸",
    "killer whale": "虎鲸",
    "sperm whale": "抹香鲸",
    "blue whale": "蓝鲸",
    "octopus": "章鱼",
    "jellyfish": "水母",
    "submarine": "潜艇 深海",
    "underwater": "水下 实拍",
    "jaguar": "美洲豹 捕猎",
    "caiman": "凯门鳄",
    "crocodile": "鳄鱼",
    "alligator": "短吻鳄",
    "anaconda": "森蚺",
    "python": "巨蟒",
    "cobra": "眼镜王蛇",
    "black mamba": "黑曼巴蛇 毒蛇",
    "secretary bird": "蛇鹫 捕蛇",
    "cheetah": "猎豹 捕猎",
    "gazelle": "瞪羚",
    "honey badger": "蜜獾 平头哥",
    "lion": "狮子 捕猎",
    "tiger": "老虎 实拍",
    "leopard": "花豹",
    "wolf": "狼群",
    "bear": "棕熊",
    "eagle": "老鹰 捕食",
    "komodo dragon": "科莫多巨蜥",
    "mantis shrimp": "雀尾螳螂虾",
    "electric eel": "电鳗",
    "piranha": "食人鱼",
    "roman empire": "罗马帝国 历史",
    "ancient egypt": "古埃及 历史",
    "pyramid": "金字塔 揭秘",
    "world war": "二战 历史",
    "cold war": "冷战 历史",
    "atlantis": "亚特兰蒂斯",
    "chernobyl": "切尔诺贝利",
    "titanic": "泰坦尼克号 3D",
    "black hole": "黑洞 宇宙 3D",
    "psychology": "心理学 效应",
    "body language": "微表情 肢体语言",
    "lie detector": "测谎 微表情",
    "fbi": "FBI 审讯 肢体语言",
    "manipulation": "暗黑心理学 操控",
    "narcissist": "自恋型人格 心理学",
    # Ngách 01: Cứu hộ động vật & Chó nghiệp vụ / Chó cứu hộ sinh tồn (Paws & Hearts Rescue)
    "newfoundland dog": "纽芬兰犬 水上救援",
    "newfoundland": "纽芬兰犬 救生",
    "st bernard": "圣伯纳犬 搜救",
    "saint bernard": "圣伯纳犬 搜救",
    "search and rescue dog": "搜救犬 救援 实拍",
    "rescue dog": "搜救犬 救助",
    "water rescue": "水上救援犬 实拍",
    "avalanche rescue": "雪崩 搜救犬",
    "german shepherd": "德牧 警犬 搜救",
    "belgian malinois": "马犬 警犬 飞跃",
    "malinois": "马犬 警犬",
    "golden retriever": "金毛 搜救 导盲犬",
    "labrador": "拉布拉多 搜救犬",
    "kangal": "坎高犬 护卫犬",
    "husky": "哈士奇 雪橇犬",
    "alaskan malamute": "阿拉斯加雪橇犬",
    "police dog": "警犬 抓捕 实拍",
    "military dog": "军犬 训导 实拍",
    "guide dog": "导盲犬 实拍",
    "stray dog": "流浪狗 救助 实拍",
    "dog rescue": "流浪狗 救援 感人",
    "puppy rescue": "小狗 救助 领养",
    "kitten rescue": "流浪猫 救助 实拍",
    "stray cat": "流浪猫 救援",
    "animal rescue": "动物救援 野生动物救助",
    "shelter dog": "收容所 流浪狗 领养",
    "drowning swimmer": "落水 救援 救生犬",
}

# Các từ khóa rác trên Bilibili cần loại bỏ (game, reacción, vlog nói chuyện, bài giảng dài)
_BILI_BLOCKLIST = (
    "游戏", "实况", "解说", "Minecraft", "我的世界", "迷你世界", "王者荣耀", "原神",
    "第五人格", "搞笑", "鬼畜", "开箱", "测评", "直播回放", "reaction", "吐槽", "教程",
    "考研", "课程", "讲座", "PPT", "有声书", "助眠", "ASMR", "科比", "篮球", "NBA",
    "球星", "曼巴精神", "宇宙黑曼巴", "宝可梦", "神奇宝贝", "动漫", "手书", "MMD",
)


def is_bilibili_source(url_or_id: str) -> bool:
    """Kiểm tra xem chuỗi đầu vào có phải là link Bilibili hoặc mã BV/av không."""
    s = str(url_or_id or "").strip()
    if not s:
        return False
    if "bilibili.com" in s.lower() or "b23.tv" in s.lower():
        return True
    if _BVID_RE.match(s) or _AVID_RE.match(s):
        return True
    return False


def extract_bilibili_id(url_or_id: str) -> Optional[Dict[str, str]]:
    """
    Trích xuất mã `bvid` (dạng BV1...) hoặc `aid` (dạng số av...) từ URL hoặc chuỗi ID.
    Hỗ trợ tự động giải nén link rút gọn `b23.tv`.
    """
    s = str(url_or_id or "").strip()
    if not s:
        return None

    if "b23.tv/" in s.lower():
        try:
            r = requests.head(s, headers=_BILI_HEADERS, allow_redirects=True, timeout=6)
            s = r.url or s
        except Exception:
            pass

    m_bv = _BVID_RE.search(s)
    if m_bv:
        return {"type": "bv", "id": m_bv.group(1), "bvid": m_bv.group(1)}

    m_av = _AVID_RE.search(s)
    if m_av:
        return {"type": "av", "id": m_av.group(1), "aid": m_av.group(1)}

    return None


def _parse_bili_duration(dur_val: Any) -> float:
    """Chuyển đổi thời lượng Bilibili ('3:51' hoặc '01:02:15' hoặc số giây) sang float giây."""
    if isinstance(dur_val, (int, float)):
        return float(dur_val)
    raw = str(dur_val or "").strip()
    if not raw:
        return 0.0
    if raw.isdigit():
        return float(int(raw))
    parts = raw.split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except Exception:
        pass
    return 0.0


def translate_query_to_chinese(query: str, primary_entity: str = "", is_3d_or_scifi: bool = False) -> str:
    """
    Chuyển đổi truy vấn tìm kiếm cảnh quay (EN / PT / VI) sang từ khóa tiếng Trung giản thể (`zh-CN`)
    sát nghĩa nhất để tìm video tài liệu / 3D / cảnh thật trên Bilibili.
    """
    clean_q = re.sub(r"\s+", " ", f"{query} {primary_entity}".strip()).lower()
    if not clean_q:
        return "自然纪录片 高清"

    # Nếu bản thân query đã có chữ Hán thì dùng luôn
    if re.search(r"[\u4e00-\u9fff]", clean_q):
        return query.strip()

    with _bili_lock:
        if clean_q in _zh_query_cache:
            return _zh_query_cache[clean_q]

    # 1. Tra cứu nhanh từ điển thuật ngữ cốt lõi — sắp xếp theo vị trí xuất hiện sớm nhất trong câu truy vấn của cảnh (query trước, primary_entity sau)
    matched_pairs: List[Tuple[int, str]] = []
    seen_zh: set[str] = set()
    for en_term, zh_term in _CORE_TERM_ZH_MAP.items():
        pos = clean_q.find(en_term)
        if pos >= 0 and zh_term not in seen_zh:
            seen_zh.add(zh_term)
            matched_pairs.append((pos, zh_term))
    matched_pairs.sort(key=lambda x: x[0])
    matched_zh = [zt for _, zt in matched_pairs]

    if matched_zh and not is_3d_or_scifi:
        zh_fast = matched_zh[0].strip() if " " in matched_zh[0] else " ".join(matched_zh[:2]).strip()
        with _bili_lock:
            _zh_query_cache[clean_q] = zh_fast
        return zh_fast

    # 2. Gọi nhanh Gemini Flash (với thinkingBudget=0, timeout=3.5s) để dịch chuẩn ngữ cảnh
    zh_result = ""
    try:
        from config.settings import settings
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            style_rule = (
                "append '3D 动画' at the end"
                if is_3d_or_scifi
                else "ONLY output 2 to 3 core subject + physical action words, NEVER append generic filler words like 高清, 实拍, or 纪录片"
            )
            prompt = (
                f"Translate this video B-roll search query into a concise Simplified Chinese (zh-CN) "
                f"Bilibili search query ({style_rule}, use exact Chinese biological/scientific species terminology "
                f"instead of literal word-by-word translation, e.g. 'secretary bird' -> '蛇鹫', "
                f"no English words unless proper acronym like FBI/NASA).\n"
                f"Input query: \"{query}\" (Primary subject: \"{primary_entity}\")\n"
                f"Return ONLY valid JSON: {{\"zh_query\": \"...\"}}"
            )
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=4):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1),
                }
                try:
                    resp = requests.post(url, json=payload, timeout=(3.0, 5.5))
                    if resp.status_code == 200:
                        cands = resp.json().get("candidates") or []
                        if cands:
                            parts_list = cands[0].get("content", {}).get("parts", [])
                            txt = "".join(
                                str(p.get("text") or "")
                                for p in parts_list
                                if isinstance(p, dict) and not p.get("thought")
                            ).strip()
                            if not txt and parts_list:
                                txt = str(parts_list[-1].get("text") or "").strip()
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                data = json.loads(m.group(0))
                                q_out = str(data.get("zh_query") or "").strip()
                                if q_out and re.search(r"[\u4e00-\u9fff]", q_out):
                                    zh_result = q_out
                                    break
                    elif resp.status_code in (429, 403, 404, 503, 400):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as exc:
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(exc))
    except Exception:
        pass

    if not zh_result:
        if matched_zh:
            suffix = "3D 科普" if is_3d_or_scifi else "纪录片 实拍"
            zh_result = f"{' '.join(matched_zh[:2])} {suffix}"
        else:
            zh_result = f"{primary_entity or query} 纪录片 高清".strip()

    with _bili_lock:
        _zh_query_cache[clean_q] = zh_result
    return zh_result


def search_bilibili_videos(
    keyword: str,
    max_results: int = 12,
    min_dur_s: float = 8.0,
    max_dur_s: float = 600.0,
) -> List[Dict[str, Any]]:
    """
    Tìm kiếm video trên Bilibili trực tiếp qua API `/x/web-interface/search/type` (~0.7s).
    Trả về danh sách ứng viên đã lọc bỏ video rác và sắp xếp theo lượt xem giảm dần.
    """
    kw = str(keyword or "").strip()
    if not kw:
        return []

    cache_key = f"{kw.lower()}::{int(min_dur_s)}::{int(max_dur_s)}"
    with _bili_lock:
        if cache_key in _bili_search_cache:
            return list(_bili_search_cache[cache_key][:max_results])

    results: List[Dict[str, Any]] = []
    try:
        sess = requests.Session()
        sess.headers.update(_BILI_HEADERS)
        sess.cookies.set("buvid3", f"{uuid.uuid4()}infoc", domain=".bilibili.com")

        resp = sess.get(
            "https://api.bilibili.com/x/web-interface/search/type",
            params={"search_type": "video", "keyword": kw, "page": 1},
            timeout=(3.5, 6.0),
        )
        if resp.status_code == 200:
            raw_items = ((resp.json().get("data") or {}).get("result")) or []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid") or "").strip()
                aid = str(item.get("aid") or "").strip()
                if not bvid and not aid:
                    continue
                clean_title = _HTML_TAG_RE.sub("", str(item.get("title") or "")).strip()
                author = str(item.get("author") or "").strip()
                desc = str(item.get("description") or "").strip()
                dur_s = _parse_bili_duration(item.get("duration"))
                if dur_s < min_dur_s or dur_s > max_dur_s:
                    continue
                meta_text = f"{clean_title} {author} {desc}"
                if any(bad in meta_text for bad in _BILI_BLOCKLIST):
                    continue
                views = int(item.get("play") or 0)
                pic = str(item.get("pic") or "").strip()
                if pic.startswith("//"):
                    pic = "https:" + pic
                vid_url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
                results.append({
                    "id": bvid or f"av{aid}",
                    "bvid": bvid,
                    "aid": aid,
                    "title": clean_title,
                    "uploader": author,
                    "description": desc,
                    "duration": dur_s,
                    "view_count": views,
                    "thumbnail": pic,
                    "url": vid_url,
                    "source": "bilibili",
                })
    except Exception as exc:
        logger.debug(f"[BilibiliFetcher] Lỗi tìm kiếm Bilibili ('{kw}'): {exc}")

    # Lọc & ưu tiên các ứng viên chứa đúng từ khóa chủ thể chữ Hán đầu tiên trong tiêu đề/mô tả (bỏ qua từ phụ chung chung)
    _generic_zh_fillers = {"纪录片", "高清", "实拍", "视频", "动画", "科普", "自然"}
    zh_tokens = [
        t for t in re.findall(r"[\u4e00-\u9fff]{2,}", kw)
        if t not in _generic_zh_fillers
    ]
    primary_zh_anchor = (
        zh_tokens[0]
        if (zh_tokens and len(zh_tokens[0]) <= 4)
        else (zh_tokens[0][:3] if zh_tokens else "")
    )
    if primary_zh_anchor and results:
        anchor_matched = [
            r for r in results
            if primary_zh_anchor in f"{r.get('title', '')} {r.get('description', '')}"
        ]
        if anchor_matched:
            results = anchor_matched

    # Sắp xếp ưu tiên video khớp nhiều từ khóa chữ Hán nhất + lượt xem cao
    results.sort(
        key=lambda x: (
            sum(1 for t in zh_tokens if t in f"{x.get('title', '')} {x.get('description', '')}"),
            int(x.get("view_count") or 0),
        ),
        reverse=True,
    )

    if results:
        with _bili_lock:
            _bili_search_cache[cache_key] = results

    return results[:max_results]


def _build_bili_cookie_headers() -> Dict[str, str]:
    """T?o b? Header chu?n k?m cookie phi?n buvid3 + b_nut gi?p Bilibili API (view & playurl) tr? d? li?u 100% kh?ng b? ch?n 400/412."""
    hdrs = dict(_BILI_HEADERS)
    hdrs["Cookie"] = f"buvid3={uuid.uuid4()}infoc; b_nut={int(time.time())}; CURRENT_FNVAL=4048"
    return hdrs


def get_bilibili_video_info_and_streams(
    url_or_id: str,
    min_height: int = 0,
    require_landscape: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Tr?ch xu?t to?n b? metadata v? URL lu?ng H.264 1080p/720p tr?c ti?p c?a Bilibili
    qua chu?i 3 t?ng d? ph?ng (Multi-Tier Bilibili Stream Extractor):
      - T?ng 1: Direct REST API `/x/web-interface/view` + `/x/player/playurl` (DASH `fnval=16&try_look=1` + HTML5 MP4 `fnval=1&platform=html5`) k?m cookie `buvid3` (~0.3s, 100% ?n ??nh).
      - T?ng 2: `yt-dlp` `BiliBiliIE` WBI (`wbi/view/detail` + `_download_playinfo(try_look=1)`) k?m cookie `buvid3`.
      - T?ng 3: H? tr? ??y ?? c? khung h?nh ?i?n ?nh Ultrawide 21:9 (`width >= 1280`, vd `1280x590`) v? t? ??ng fallback xu?ng lu?ng n?t nh?t hi?n c? n?u video g?c ch? c? 480p/360p.
    """
    parsed = extract_bilibili_id(url_or_id)
    if not parsed:
        return None

    hdrs = _build_bili_cookie_headers()
    view: Dict[str, Any] = {}
    video_formats: List[Dict[str, Any]] = []
    audio_formats: List[Dict[str, Any]] = []

    # ?? T?NG 1: Direct Bilibili REST API (/x/web-interface/view + /x/player/playurl) ??
    try:
        v_params: Dict[str, Any] = {"bvid": parsed["bvid"]} if parsed["type"] == "bv" else {"aid": parsed["aid"]}
        r_view = requests.get(
            "https://api.bilibili.com/x/web-interface/view",
            params=v_params,
            headers=hdrs,
            timeout=(3.5, 6.0),
        )
        if r_view.status_code == 200:
            j_view = r_view.json()
            if j_view.get("code") == 0 and isinstance(j_view.get("data"), dict):
                view = j_view["data"]
    except Exception as exc_v:
        logger.debug(f"[BilibiliFetcher] Direct view API l?i nh? ({url_or_id}): {exc_v}")

    # ?? T?NG 2: Fallback sang yt-dlp BiliBiliIE WBI n?u T?ng 1 ch?a l?y ???c view ??
    ie = None
    if not view:
        try:
            import yt_dlp
            from yt_dlp.extractor.bilibili import BiliBiliIE

            ydl = yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 8,
                "http_headers": hdrs,
            })
            ie = BiliBiliIE(ydl)
            query: Dict[str, Any] = {"platform": "web"}
            vid_key = parsed["id"]
            if parsed["type"] == "bv":
                query["bvid"] = parsed["bvid"]
            else:
                query["aid"] = parsed["aid"]

            detail = ie._download_json(
                "https://api.bilibili.com/x/web-interface/wbi/view/detail",
                vid_key,
                query=ie._sign_wbi(query, vid_key),
                headers=hdrs,
            )
            view = ((detail or {}).get("data") or {}).get("View") or {}
        except Exception as exc_wbi:
            logger.debug(f"[BilibiliFetcher] WBI view detail fallback l?i ({url_or_id}): {exc_wbi}")

    if not view:
        return None

    dim = view.get("dimension") or {}
    dim_w = int(dim.get("width") or 0)
    dim_h = int(dim.get("height") or 0)
    if require_landscape and dim_w > 0 and dim_h > 0 and dim_w <= dim_h:
        logger.debug(f"[BilibiliFetcher] B? qua video d?c/vu?ng {dim_w}x{dim_h} khi y?u c?u 16:9 ({url_or_id})")
        return None

    bvid = str(view.get("bvid") or parsed.get("bvid") or "")
    aid = str(view.get("aid") or parsed.get("aid") or "")
    cid = view.get("cid")
    if (not bvid and not aid) or not cid:
        return None

    # ?? T?NG 1B: L?y lu?ng DASH (fnval=16 + try_look=1) qua Direct API ??
    try:
        p_params: Dict[str, Any] = {
            "cid": cid,
            "qn": 80,
            "fnval": 16,
            "fourk": 1,
            "try_look": 1,
        }
        if bvid:
            p_params["bvid"] = bvid
        else:
            p_params["avid"] = aid
        r_play = requests.get(
            "https://api.bilibili.com/x/player/playurl",
            params=p_params,
            headers=hdrs,
            timeout=(3.5, 6.0),
        )
        if r_play.status_code == 200:
            p_data = (r_play.json().get("data")) or {}
            dash = p_data.get("dash") or {}
            for v in (dash.get("video") or []):
                v_url = v.get("baseUrl") or v.get("base_url")
                if not v_url:
                    continue
                v_codec = str(v.get("codecs") or "avc1")
                video_formats.append({
                    "url": v_url,
                    "width": int(v.get("width") or dim_w or 1920),
                    "height": int(v.get("height") or dim_h or 1080),
                    "vcodec": v_codec,
                    "tbr": float(v.get("bandwidth") or 0.0) / 1000.0,
                    "stream_type": "dash",
                })
            for a in (dash.get("audio") or []):
                a_url = a.get("baseUrl") or a.get("base_url")
                if not a_url:
                    continue
                audio_formats.append({
                    "url": a_url,
                    "acodec": str(a.get("codecs") or "mp4a"),
                    "vcodec": "none",
                    "abr": float(a.get("bandwidth") or 0.0) / 1000.0,
                })
            if not video_formats:
                for d in (p_data.get("durl") or []):
                    if d.get("url"):
                        video_formats.append({
                            "url": d["url"],
                            "width": dim_w or 1280,
                            "height": dim_h or 720,
                            "vcodec": "avc1",
                            "tbr": float(d.get("size") or 0.0) / 1000.0,
                            "stream_type": "durl",
                        })
    except Exception as exc_p:
        logger.debug(f"[BilibiliFetcher] Direct playurl DASH l?i nh? ({bvid}): {exc_p}")

    # ?? T?NG 2B: Fallback sang HTML5 MP4 (fnval=1&platform=html5) ho?c yt-dlp extract_formats ??
    if not video_formats:
        try:
            h5_params: Dict[str, Any] = {
                "cid": cid,
                "qn": 80,
                "fnval": 1,
                "fourk": 1,
                "platform": "html5",
                "high_quality": 1,
            }
            if bvid:
                h5_params["bvid"] = bvid
            else:
                h5_params["avid"] = aid
            r_h5 = requests.get(
                "https://api.bilibili.com/x/player/playurl",
                params=h5_params,
                headers=hdrs,
                timeout=(3.5, 6.0),
            )
            if r_h5.status_code == 200:
                durls = ((r_h5.json().get("data") or {}).get("durl")) or []
                for d in durls:
                    if d.get("url"):
                        video_formats.append({
                            "url": d["url"],
                            "width": dim_w or 1280,
                            "height": dim_h or 720,
                            "vcodec": "avc1",
                            "tbr": float(d.get("size") or 0.0) / 1000.0,
                            "stream_type": "html5_mp4",
                        })
        except Exception:
            pass

    if not video_formats and ie is not None:
        try:
            play_info = ie._download_playinfo(
                bvid,
                cid,
                headers=hdrs,
                query={"try_look": 1},
                fatal=False,
            )
            all_formats = ie.extract_formats(play_info or {})
            for f in all_formats:
                if f.get("vcodec") != "none" and f.get("url"):
                    video_formats.append(f)
                elif f.get("acodec") != "none" and f.get("vcodec") == "none" and f.get("url"):
                    audio_formats.append(f)
        except Exception:
            pass

    if not video_formats:
        return None

    # ?u ti?n H.264 (avc1) <= 1080p ?? gi?i m? si?u t?c b?ng m?i FFmpeg
    avc_formats = [
        f for f in video_formats
        if str(f.get("vcodec") or "").startswith("avc")
        and min(int(f.get("width") or 1080), int(f.get("height") or 1080)) <= 1080
    ]
    working_formats = avc_formats if avc_formats else video_formats

    if require_landscape:
        # Ch?p nh?n c? chu?n 16:9 HD (height >= 720) l?n chu?n ?i?n ?nh Ultrawide 21:9 HD (width >= 1280, vd 1280x590)
        eff_min_h = max(480, int(min_height or 0))
        landscape_all = [
            f for f in working_formats
            if int(f.get("width") or dim_w or 0) > int(f.get("height") or dim_h or 0)
        ]
        if not landscape_all:
            return None
        landscape_hd = [
            f for f in landscape_all
            if int(f.get("height") or dim_h or 0) >= eff_min_h
            or int(f.get("width") or dim_w or 0) >= (1280 if eff_min_h >= 720 else 852)
        ]
        # N?u video g?c tr?n Bilibili ch? c? b?n 480p/360p th? t? ??ng l?y b?n ngang n?t nh?t hi?n c? thay v? b?o l?i
        working_formats = landscape_hd if landscape_hd else landscape_all
    elif min_height > 0:
        hd_only = [
            f for f in working_formats
            if int(f.get("height") or 0) >= int(min_height)
            or int(f.get("width") or 0) >= int(min_height)
        ]
        if hd_only:
            working_formats = hd_only

    # ?u ti?n lu?ng 720p H.264 (long_edge 960..1366, bitrate ~400-1000kbps) ?? t?i qua CDN qu?c t? Bilibili c?c nhanh (2-4s ???c tr?n 20-30s video),
    # sau ?? n?ng n?t quang h?c Lanczos + Unsharp Mask l?n 1080p m? kh?ng bao gi? b? ngh?n b?ng th?ng nh? lu?ng 1080p bitrate cao.
    best_video = sorted(
        working_formats,
        key=lambda f: (
            1 if str(f.get("vcodec") or "").startswith("avc") else 0,
            2 if 960 <= max(int(f.get("width") or 0), int(f.get("height") or 0)) <= 1440 else (
                1 if max(int(f.get("width") or 0), int(f.get("height") or 0)) > 1440 else 0
            ),
            max(int(f.get("width") or 0), int(f.get("height") or 0)),
            float(f.get("tbr") or 0.0),
        ),
    )[-1] if working_formats else None
    best_audio = sorted(
        audio_formats,
        key=lambda f: float(f.get("abr") or f.get("tbr") or 0.0),
    )[-1] if audio_formats else None

    if not best_video:
        return None

    owner = view.get("owner") or {}
    stat = view.get("stat") or {}
    pic = str(view.get("pic") or "")
    if pic.startswith("//"):
        pic = "https:" + pic

    return {
        "source_type": "bilibili",
        "id": bvid or f"av{aid}",
        "bvid": bvid or f"av{aid}",
        "aid": aid,
        "cid": cid,
        "title": str(view.get("title") or f"Bilibili {bvid}"),
        "description": str(view.get("desc") or ""),
        "uploader": str(owner.get("name") or "Bilibili Creator"),
        "duration": float(view.get("duration") or 0.0),
        "view_count": int(stat.get("view") or 0),
        "width": int(best_video.get("width") or dim_w or 1920),
        "height": int(best_video.get("height") or dim_h or 1080),
        "tbr": float(best_video.get("tbr") or 0.0),
        "stream_type": str(best_video.get("stream_type") or "dash"),
        "thumbnail": pic,
        "webpage_url": f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}",
        "video_stream_url": best_video["url"],
        "audio_stream_url": best_audio["url"] if best_audio else None,
        "http_headers": hdrs,
    }


def download_and_crop_bilibili_broll_clip(
    url_or_id: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    max_range_bytes: int = 7 * 1024 * 1024,
    aspect_ratio: str = "9:16",
    preserve_continuous_master: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    T?i nhanh ph?n ?o?n ??u/cao tr?o t? lu?ng H.264 1080p/720p c?a Bilibili,
    sau ?? ?p d?ng **Bilibili Safe-Crop + Lanczos Optical Sharpening**:
      - V?i 16:9 Long-Form: B?t bu?c ngu?n ngang (`width > height`),
        t?i chunk 15MB?28MB ?? l?y ?? ?? d?i th?c t? (kh?ng k?o gi?n slow-mo),
        c?t tinh g?n `crop=iw*0.86:ih*0.80:iw*0.07:ih*0.09` (lo?i b? ho?n to?n watermark g?c Bilibili)
        + `scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:1080` + `unsharp=5:5:0.85:5:5:0.0`.
      - V?i 9:16 Shorts: H? tr? c? ngu?n ngang 16:9 (chuy?n sang khung Cinema 4:5 + Blur Background 1080x1920)
        l?n ngu?n d?c 9:16 nguy?n b?n tr?n Bilibili (`width <= height` -> Safe-Crop 1080x1920 full-screen).
    """
    is_16x9 = str(aspect_ratio or "9:16").strip() in ("16:9", "long", "landscape", "horizontal")
    info = get_bilibili_video_info_and_streams(
        url_or_id,
        min_height=720 if is_16x9 else 480,
        require_landscape=is_16x9,
    )
    if (not info or not info.get("video_stream_url")) and is_16x9 and not preserve_continuous_master:
        # N?u ng??i d?ng b?m c?t 16:9 tr?n Telegram cho 1 video Bilibili g?c d?c ho?c <720p, t? ??ng l?y lu?ng t?t nh?t ?? ??t v?o khung 16:9 Blur-Pillarbox
        info = get_bilibili_video_info_and_streams(url_or_id, min_height=0, require_landscape=False)
    if not info or not info.get("video_stream_url"):
        return None

    stream_url = info["video_stream_url"]
    dl_headers = dict(info.get("http_headers") or _build_bili_cookie_headers())
    tbr_kbps = float(info.get("tbr") or 0.0)
    bytes_per_sec = (tbr_kbps * 1000.0 / 8.0) if tbr_kbps > 40.0 else 180_000.0
    desired_window_s = max(14.0, float(target_duration_sec) + (12.0 if preserve_continuous_master else 6.0))
    computed_bytes = int(desired_window_s * bytes_per_sec * 1.35) + 350_000
    max_cap_bytes = 20 * 1024 * 1024 if preserve_continuous_master else 10 * 1024 * 1024
    eff_range_bytes = max(1_800_000, min(int(max_range_bytes), max_cap_bytes, computed_bytes))
    dl_headers["Range"] = f"bytes=0-{eff_range_bytes - 1}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_m4s = Path(tmp_dir) / f"bili_raw_{info['bvid']}.m4s"
        try:
            t_dl_bili = time.monotonic()
            r = requests.get(stream_url, headers=dl_headers, stream=True, timeout=(4.0, 10.0))
            if r.status_code not in (200, 206):
                dl_headers.pop("Range", None)
                r = requests.get(stream_url, headers=dl_headers, stream=True, timeout=(4.0, 10.0))
                if r.status_code not in (200, 206):
                    return None
            downloaded_bytes = 0
            with open(raw_m4s, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                    if downloaded_bytes >= eff_range_bytes or (time.monotonic() - t_dl_bili > 14.0):
                        break
            if not raw_m4s.exists() or raw_m4s.stat().st_size < 90_000:
                return None
        except Exception as exc:
            logger.debug(f"[BilibiliFetcher] T?i HTTP Range th?t b?i ({info['bvid']}): {exc}")
            return None

        from app.utils.ffmpeg_helper import (
            BT709_COLOR_ARGS,
            get_duration_ms,
            get_ffmpeg_exe,
        )

        # L?u ? quan tr?ng: Header moov c?a file DASH .m4s b?o t?ng th?i l??ng to?n video (vd 1100s),
        # nh?ng tr?n ??a ch? c? `downloaded_bytes` ??u ti?n -> T?nh ch?nh x?c th?i l??ng th?c c? trong chunk ?? t?i!
        header_dur_s = max(0.0, get_duration_ms(str(raw_m4s)) / 1000.0)
        est_chunk_dur_s = max(3.5, (downloaded_bytes / max(18_000.0, bytes_per_sec)) * 0.86)
        avail_dur_s = min(header_dur_s, est_chunk_dur_s) if header_dur_s > 0 else est_chunk_dur_s
        if avail_dur_s < 2.5:
            return None

        max_allowed_dur = (48.0 if preserve_continuous_master else 20.0) if is_16x9 else min(20.0, max(8.0, float(target_duration_sec)))
        req_dur = min(max(3.0, float(target_duration_sec)), max_allowed_dur, max(2.8, avail_dur_s - 0.5))
        spare_s = max(0.0, avail_dur_s - req_dur - 0.6)
        # Bỏ qua 4.5s–6.5s đầu tiên của video Bilibili (thường chứa thẻ tiêu đề/intro chữ Trung Quốc)
        peak_start = round(min(6.5 if spare_s >= 7.0 else (4.8 if is_16x9 else 4.2), spare_s * 0.62), 2)
        peak_len = req_dur

        clip_dur = min(max(req_dur, peak_len), max_allowed_dur, max(2.8, avail_dur_s - peak_start))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = get_ffmpeg_exe()
        src_w = int(info.get("width") or 1920)
        src_h = int(info.get("height") or 1080)

        # Bilibili Safe-Crop + Crisp Lanczos + Optical Unsharp Mask:
        if is_16x9 and src_w <= src_h:
            fc_16x9_pillar = (
                "[0:v]crop=iw*0.88:ih*0.82:iw*0.06:ih*0.08,split=2[v_fg][v_bg];"
                "[v_bg]scale=1920:1080:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                "crop=1920:1080,boxblur=24:4,eq=brightness=-0.16:saturation=1.15[bg];"
                "[v_fg]scale=1080:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1,"
                "unsharp=5:5:0.85:5:5:0.0,eq=contrast=1.04:saturation=1.06[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[vout]"
            )
            cmd = [
                ffmpeg_bin, "-y", "-v", "error",
                "-ss", f"{max(0.0, peak_start):.2f}",
                "-t", f"{clip_dur:.2f}",
                "-i", str(raw_m4s),
                "-filter_complex", fc_16x9_pillar,
                "-map", "[vout]",
                "-an",
                "-threads", "2",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                *BT709_COLOR_ARGS,
                str(output_path),
            ]
        elif is_16x9:
            base_16x9_vf = (
                "crop=iw*0.86:ih*0.78:iw*0.07:ih*0.09,"
                "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=1920:1080,"
                "unsharp=5:5:0.85:5:5:0.0,"
                "eq=contrast=1.04:saturation=1.05,"
                "setsar=1,fps=30,format=yuv420p"
            )
            half_d = round(clip_dur * 0.5, 2)
            rem_d = round(clip_dur - half_d, 2)
            second_start = round(peak_start + half_d + 3.0, 2)
            if (not preserve_continuous_master) and clip_dur >= 9.0 and avail_dur_s >= (second_start + rem_d + 0.4):
                fc = (
                    f"[0:v]setpts=PTS-STARTPTS,{base_16x9_vf}[v1];"
                    f"[1:v]setpts=PTS-STARTPTS,{base_16x9_vf},"
                    f"scale=2150:1210:flags=lanczos,crop=1920:1080:115:65,setsar=1[v2];"
                    f"[v1][v2]concat=n=2:v=1:a=0,fps=30,format=yuv420p[vout]"
                )
                cmd = [
                    ffmpeg_bin, "-y", "-v", "error",
                    "-ss", f"{max(0.0, peak_start):.2f}", "-t", f"{half_d:.2f}", "-i", str(raw_m4s),
                    "-ss", f"{max(0.0, second_start):.2f}", "-t", f"{rem_d:.2f}", "-i", str(raw_m4s),
                    "-filter_complex", fc,
                    "-map", "[vout]",
                    "-t", f"{clip_dur:.2f}",
                    "-an",
                    "-threads", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                    *BT709_COLOR_ARGS,
                    str(output_path),
                ]
            else:
                cmd = [
                    ffmpeg_bin, "-y", "-v", "error",
                    "-ss", f"{max(0.0, peak_start):.2f}",
                    "-t", f"{clip_dur:.2f}",
                    "-i", str(raw_m4s),
                    "-vf", base_16x9_vf,
                    "-an",
                    "-threads", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                    *BT709_COLOR_ARGS,
                    str(output_path),
                ]
        else:
            if src_w <= src_h:
                # Ngu?n Bilibili v?n ?? l? video d?c 9:16 -> Safe-Crop 8% vi?n g?c v? ph?ng ??y 1080x1920
                vf_vert = (
                    "crop=iw*0.88:ih*0.82:iw*0.06:ih*0.08,"
                    "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=1080:1920,"
                    "unsharp=5:5:0.85:5:5:0.0,eq=contrast=1.04:saturation=1.06,"
                    "setsar=1,fps=30,format=yuv420p"
                )
                cmd = [
                    ffmpeg_bin, "-y", "-v", "error",
                    "-ss", f"{max(0.0, peak_start):.2f}",
                    "-t", f"{clip_dur:.2f}",
                    "-i", str(raw_m4s),
                    "-vf", vf_vert,
                    "-an",
                    "-threads", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                    *BT709_COLOR_ARGS,
                    str(output_path),
                ]
            else:
                fc_short = (
                    "[0:v]crop=iw*0.86:ih*0.68:iw*0.07:ih*0.10,split=2[v_fg][v_bg];"
                    "[v_bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                    "crop=1080:1920,boxblur=24:4,eq=brightness=-0.16:saturation=1.15[bg];"
                    "[v_fg]scale=1080:1320:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=1080:1320,unsharp=5:5:0.85:5:5:0.0,eq=contrast=1.04:saturation=1.06[fg];"
                    "[bg][fg]overlay=0:(H-h)/2,setsar=1,fps=30,format=yuv420p[vout]"
                )
                cmd = [
                    ffmpeg_bin, "-y", "-v", "error",
                    "-ss", f"{max(0.0, peak_start):.2f}",
                    "-t", f"{clip_dur:.2f}",
                    "-i", str(raw_m4s),
                    "-filter_complex", fc_short,
                    "-map", "[vout]",
                    "-an",
                    "-threads", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                    *BT709_COLOR_ARGS,
                    str(output_path),
                ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=45)
            out_p = Path(output_path)
            if out_p.exists() and out_p.stat().st_size > 35_000:
                info["clipped_duration"] = round(get_duration_ms(str(out_p)) / 1000.0, 1)
                return info
        except Exception as exc:
            logger.warning(f"[BilibiliFetcher] FFmpeg Safe-Crop th?t b?i ({info['bvid']}): {exc}")

    return None


def download_full_bilibili_video(url_or_id: str, output_path: str) -> Optional[Dict[str, Any]]:
    """
    Tải trọn vẹn cả luồng hình (1080p/720p H.264) và luồng tiếng của 1 video Bilibili,
    sau đó ghép nguyên bản (`-c copy`) thành file `.mp4` phục vụ `VideoRepurposerService`.
    """
    info = get_bilibili_video_info_and_streams(url_or_id)
    if not info or not info.get("video_stream_url"):
        return None

    headers = dict(info.get("http_headers") or _BILI_HEADERS)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        v_tmp = Path(tmp_dir) / "video.m4s"
        a_tmp = Path(tmp_dir) / "audio.m4s"

        def _stream_to_file(u: str, dst: Path) -> bool:
            try:
                with requests.get(u, headers=headers, stream=True, timeout=(5.0, 60.0)) as r:
                    if r.status_code not in (200, 206):
                        return False
                    with open(dst, "wb") as f:
                        for chunk in r.iter_content(chunk_size=524288):
                            if chunk:
                                f.write(chunk)
                return dst.exists() and dst.stat().st_size > 30_000
            except Exception as e_dl:
                logger.warning(f"[BilibiliFetcher] Lỗi tải stream Bilibili: {e_dl}")
                return False

        if not _stream_to_file(info["video_stream_url"], v_tmp):
            return None

        has_audio = bool(info.get("audio_stream_url")) and _stream_to_file(info["audio_stream_url"], a_tmp)
        if has_audio:
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(v_tmp),
                "-i", str(a_tmp),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_p),
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(v_tmp),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_p),
            ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            if out_p.exists() and out_p.stat().st_size > 50_000:
                return info
        except Exception as exc:
            logger.warning(f"[BilibiliFetcher] Lỗi ghép MP4 Bilibili ({info['bvid']}): {exc}")

    return None
