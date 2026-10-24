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
}

# Các từ khóa rác trên Bilibili cần loại bỏ (game, reacción, vlog nói chuyện, bài giảng dài)
_BILI_BLOCKLIST = (
    "游戏", "实况", "解说", "Minecraft", "我的世界", "迷你世界", "王者荣耀", "原神",
    "第五人格", "搞笑", "鬼畜", "开箱", "测评", "直播回放", "reaction", "吐槽", "教程",
    "考研", "课程", "讲座", "PPT", "有声书", "助眠", "ASMR",
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
    clean_q = re.sub(r"\s+", " ", f"{primary_entity} {query}".strip()).lower()
    if not clean_q:
        return "自然纪录片 高清"

    # Nếu bản thân query đã có chữ Hán thì dùng luôn
    if re.search(r"[\u4e00-\u9fff]", clean_q):
        return query.strip()

    with _bili_lock:
        if clean_q in _zh_query_cache:
            return _zh_query_cache[clean_q]

    # 1. Tra cứu nhanh từ điển thuật ngữ cốt lõi
    matched_zh: List[str] = []
    for en_term, zh_term in _CORE_TERM_ZH_MAP.items():
        if en_term in clean_q and zh_term not in matched_zh:
            matched_zh.append(zh_term)

    # 2. Gọi nhanh Gemini Flash (với thinkingBudget=0, timeout=3.5s) để dịch chuẩn ngữ cảnh
    zh_result = ""
    try:
        from config.settings import settings
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            style_hint = "3D动画 科普 纪录片" if is_3d_or_scifi else "高清 实拍 纪录片"
            prompt = (
                f"Translate this video B-roll search query into a concise Simplified Chinese (zh-CN) "
                f"Bilibili search query (2 to 5 Chinese words, append '{style_hint}' if appropriate, "
                f"no English words unless proper acronym like FBI/NASA).\n"
                f"Input query: \"{query}\" (Primary subject: \"{primary_entity}\")\n"
                f"Return ONLY valid JSON: {{\"zh_query\": \"...\"}}"
            )
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=2):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1),
                }
                try:
                    resp = requests.post(url, json=payload, timeout=(2.5, 4.0))
                    if resp.status_code == 200:
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
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

    # Sắp xếp ưu tiên video có lượt xem cao (chất lượng hình ảnh/3D đã được kiểm chứng trên Bilibili)
    results.sort(key=lambda x: int(x.get("view_count") or 0), reverse=True)

    if results:
        with _bili_lock:
            _bili_search_cache[cache_key] = results

    return results[:max_results]


def get_bilibili_video_info_and_streams(url_or_id: str) -> Optional[Dict[str, Any]]:
    """
    Trích xuất toàn bộ metadata và URL luồng H.264 1080p/720p trực tiếp của Bilibili
    bằng chữ ký WBI (`wbi/view/detail` + `_download_playinfo(try_look=1)`).
    Hoạt động ổn định 100% kể cả khi Bilibili ẩn `window.__INITIAL_STATE__` khỏi HTML.
    """
    parsed = extract_bilibili_id(url_or_id)
    if not parsed:
        return None

    try:
        import yt_dlp
        from yt_dlp.extractor.bilibili import BiliBiliIE

        ydl = yt_dlp.YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 8,
            "http_headers": dict(_BILI_HEADERS),
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
            headers=dict(_BILI_HEADERS),
        )
        view = ((detail or {}).get("data") or {}).get("View") or {}
        if not view:
            return None

        bvid = str(view.get("bvid") or parsed.get("bvid") or "")
        aid = str(view.get("aid") or parsed.get("aid") or "")
        cid = view.get("cid")
        if not bvid or not cid:
            return None

        play_info = ie._download_playinfo(
            bvid,
            cid,
            headers=dict(_BILI_HEADERS),
            query={"try_look": 1},
            fatal=False,
        )
        all_formats = ie.extract_formats(play_info or {})

        video_formats = [
            f for f in all_formats
            if f.get("vcodec") != "none"
            and str(f.get("vcodec") or "").startswith("avc")
            and f.get("url")
            and int(f.get("height") or 0) <= 1080
        ]
        if not video_formats:
            video_formats = [
                f for f in all_formats
                if f.get("vcodec") != "none" and f.get("url")
            ]
        audio_formats = [
            f for f in all_formats
            if f.get("acodec") != "none" and f.get("vcodec") == "none" and f.get("url")
        ]

        best_video = sorted(
            video_formats,
            key=lambda f: (int(f.get("height") or 0), float(f.get("tbr") or 0.0)),
        )[-1] if video_formats else None
        best_audio = sorted(
            audio_formats,
            key=lambda f: float(f.get("abr") or f.get("tbr") or 0.0),
        )[-1] if audio_formats else None

        if not best_video:
            return None

        owner = view.get("owner") or {}
        stat = view.get("stat") or {}
        dim = view.get("dimension") or {}
        pic = str(view.get("pic") or "")
        if pic.startswith("//"):
            pic = "https:" + pic

        return {
            "source_type": "bilibili",
            "id": bvid,
            "bvid": bvid,
            "aid": aid,
            "cid": cid,
            "title": str(view.get("title") or f"Bilibili {bvid}"),
            "description": str(view.get("desc") or ""),
            "uploader": str(owner.get("name") or "Bilibili Creator"),
            "duration": float(view.get("duration") or 0.0),
            "view_count": int(stat.get("view") or 0),
            "width": int(dim.get("width") or best_video.get("width") or 1920),
            "height": int(dim.get("height") or best_video.get("height") or 1080),
            "thumbnail": pic,
            "webpage_url": f"https://www.bilibili.com/video/{bvid}",
            "video_stream_url": best_video["url"],
            "audio_stream_url": best_audio["url"] if best_audio else None,
            "http_headers": dict(_BILI_HEADERS),
        }
    except Exception as exc:
        logger.debug(f"[BilibiliFetcher] Không lấy được luồng Bilibili cho '{url_or_id}': {exc}")
        return None


def download_and_crop_bilibili_broll_clip(
    url_or_id: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    max_range_bytes: int = 7 * 1024 * 1024,
) -> Optional[Dict[str, Any]]:
    """
    Tải nhanh phân đoạn đầu/cao trào (6-7MB qua HTTP Range ~1.5s) từ luồng H.264 1080p/720p của Bilibili,
    sau đó áp dụng **Bilibili Safe-Crop (`crop=iw:ih*0.78:0:ih*0.08`)** cắt bỏ hoàn toàn:
      - 8% mép trên (logo Bilibili & tên kênh góc trên)
      - 14% mép dưới (phụ đề tiếng Trung in cứng ở đáy màn hình)
    rồi chuyển về chuẩn dọc 9:16 (`1080x1920`), tắt tiếng gốc (`-an`) và khóa màu `BT.709`.
    """
    info = get_bilibili_video_info_and_streams(url_or_id)
    if not info or not info.get("video_stream_url"):
        return None

    stream_url = info["video_stream_url"]
    dl_headers = dict(info.get("http_headers") or _BILI_HEADERS)
    dl_headers["Range"] = f"bytes=0-{int(max_range_bytes) - 1}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_m4s = Path(tmp_dir) / f"bili_raw_{info['bvid']}.m4s"
        try:
            r = requests.get(stream_url, headers=dl_headers, stream=True, timeout=(4.0, 9.0))
            if r.status_code not in (200, 206):
                return None
            with open(raw_m4s, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
            if not raw_m4s.exists() or raw_m4s.stat().st_size < 150_000:
                return None
        except Exception as exc:
            logger.debug(f"[BilibiliFetcher] Tải HTTP Range thất bại ({info['bvid']}): {exc}")
            return None

        from app.utils.ffmpeg_helper import (
            BT709_COLOR_ARGS,
            find_hook_peak_window,
            get_duration_ms,
            get_video_encoder_args,
        )

        avail_dur_s = max(0.0, get_duration_ms(str(raw_m4s)) / 1000.0)
        if avail_dur_s < 3.2:
            return None

        req_dur = min(max(3.2, float(target_duration_sec)), max(3.2, avail_dur_s - 0.5))
        try:
            peak_start, peak_len = find_hook_peak_window(
                str(raw_m4s),
                min_duration=req_dur,
                max_duration=min(8.5, avail_dur_s),
            )
        except Exception:
            peak_start = min(2.0, max(0.0, avail_dur_s - req_dur))
            peak_len = req_dur

        clip_dur = min(max(req_dur, peak_len), 8.0, max(3.2, avail_dur_s - peak_start))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Bilibili Safe-Crop:
        # Cắt bỏ 8% đỉnh (logo Bilibili) + 14% đáy (phụ đề cứng tiếng Trung), giữ 78% vùng trung tâm sạch
        vf_chain = (
            "crop=iw:ih*0.78:0:ih*0.08,"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "setsar=1,fps=30"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{max(0.0, peak_start):.2f}",
            "-t", f"{clip_dur:.2f}",
            "-i", str(raw_m4s),
            "-vf", vf_chain,
            "-an",
            *get_video_encoder_args(quality="intermediate"),
            *BT709_COLOR_ARGS,
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=25)
            out_p = Path(output_path)
            if out_p.exists() and out_p.stat().st_size > 40_000:
                return info
        except Exception as exc:
            logger.debug(f"[BilibiliFetcher] FFmpeg Safe-Crop thất bại ({info['bvid']}): {exc}")

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
