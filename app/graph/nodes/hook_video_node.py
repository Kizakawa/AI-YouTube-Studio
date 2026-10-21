"""
app.graph.nodes.hook_video_node — Node chuyên biệt tạo Hook (Chạy TRƯỚC `node_fetch_media`):
Tích hợp trực tiếp toàn bộ quy trình vào trong hàm tạo Hook `node_produce_and_score_hook_video`:
1. Cổng phân loại Hook bằng Gemini (`hook_visual_mode`: `"video"` hay `"scene"`, `is_abstract_keyword`, `climax_scene_id`):
   - Gemini đánh giá kịch bản/câu Hook và trả về trực tiếp xem Hook này nên TẠO VIDEO (`"video"` — săn & cắt đoạn Peak từ YouTube Shorts)
     hay TẠO CẢNH (`"scene"` — bỏ qua tìm video Short trên mạng, chuyển sang tạo cảnh / tìm ảnh truyền thống khi từ khóa trừu tượng).
2. Khi Gemini quyết định `"video"`:
   - Tự động search các video Short trên mạng (`yt-dlp`, tải nguyên vẹn Short <= 75s).
   - Quét toàn bộ chiều dài Short bằng `find_hook_peak_action_offset` để tìm đúng ĐOẠN PEAK NHẤT & TRỪU TƯỢNG/CUỐN HÚT NHẤT (`peak_offset_sec`).
   - Trích xuất 3 khung hình NGAY TẠI ĐOẠN PEAK (`-ss peak_offset_sec`, tuyệt đối không lấy ở giây `0.0s` lúc video gốc chưa vào cao trào)
     và gửi cho Gemini Vision chấm điểm + quyết định `"recommended_hook_mode": "video" | "scene"`.
   - Nếu ĐẠT (`"video"`): Cắt đoạn Peak ghép vào ĐẦU VIDEO (Cảnh 1) và ghép vào TRONG THÂN VIDEO tại Cảnh Cao Trào (`climax_scene_id`).
   - Nếu KHÔNG ĐẠT (hoặc Gemini trả về `"scene"`): Chuyển về tạo cảnh / tìm ảnh truyền thống tại `node_fetch_media`.
"""

import os
import re
import json
import base64
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from config.settings import settings
from app.graph.state import VideoWorkflowState
from app.services.media import license_policy
from app.utils.langsmith_tracer import trace_custom_span
from app.utils.ffmpeg_helper import (
    trim_video,
    render_opening_hook_multi_cut,
    find_hook_peak_action_offset,
    find_hook_peak_window,
    VIDEO_DURATION_SAFETY_MARGIN,
)
from app.services.media.media_history import (
    extract_media_identity,
    get_recent_media_urls,
    is_media_already_used,
    record_used_media,
)

logger = logging.getLogger(__name__)


def _build_hook_video_search_queries(
    topic: str,
    primary_entity: str,
    visual_keyword: str,
    niche: str,
) -> List[str]:
    """Tạo danh sách truy vấn dự phòng ngắn gọn (2-5 từ) khi cần tìm kiếm video Short hành động thực tế."""
    queries: List[str] = []
    clean_kw = re.sub(r"[^a-zA-Z0-9\s]", " ", visual_keyword or "").strip()
    clean_ent = re.sub(r"[^a-zA-Z0-9\s]", " ", primary_entity or "").strip()
    clean_top = re.sub(r"[^a-zA-Z0-9\s]", " ", topic or "").strip()

    stop_words = {
        "cinematic", "photorealistic", "dramatic", "lighting", "4k", "8k", "vertical",
        "why", "how", "what", "when", "secret", "behind", "truth", "about", "never",
    }
    kw_tokens = [w for w in clean_kw.split() if w.lower() not in stop_words][:4]
    ent_tokens = [w for w in clean_ent.split() if w.lower() not in stop_words][:3]
    top_tokens = [w for w in clean_top.split() if w.lower() not in stop_words][:4]

    niche_l = (niche or "").lower()
    action_suffix = "wildlife action" if any(k in niche_l for k in ("wildlife", "predator")) else "footage"

    if ent_tokens and kw_tokens:
        merged = " ".join(dict.fromkeys(ent_tokens + kw_tokens[:2]))
        queries.append(f"{merged} {action_suffix}".strip())
    if kw_tokens:
        queries.append(" ".join(kw_tokens))
    if top_tokens:
        queries.append(" ".join(top_tokens))

    unique_q = []
    for q in queries:
        q_clean = re.sub(r"\s+", " ", q).strip()
        if q_clean and q_clean.lower() not in [x.lower() for x in unique_q]:
            unique_q.append(q_clean)
    return unique_q[:2] or ["cinematic wildlife action"]


@trace_custom_span("HookVideoNode.produce_and_score_hook", run_type="chain")
async def node_produce_and_score_hook_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Hàm Tạo Hook Chuyên Biệt (`node_produce_and_score_hook_video` — Chạy TRƯỚC `node_fetch_media`):
    Tích hợp trực tiếp cả 4 bước ngay bên trong hàm tạo Hook:
    1. Gemini quyết định `hook_visual_mode`: `"video"` (săn & cắt video Short) hay `"scene"` (tạo cảnh / tìm ảnh truyền thống)
       cùng `is_abstract_keyword` (True/False) và `climax_scene_id` (vị trí cảnh cao trào trong thân video).
    2. Nếu Gemini trả về `"scene"` (hoặc `is_abstract_keyword == True`):
       -> Bỏ qua tìm kiếm video Short trên mạng, chuyển thẳng sang tạo cảnh / tìm ảnh truyền thống tại `node_fetch_media`.
    3. Nếu Gemini trả về `"video"`:
       - Tải các video Short ứng viên trên mạng (`yt-dlp`, thời lượng <= 75s).
       - Quét toàn bộ video Short bằng `find_hook_peak_action_offset` để tìm đúng mốc thời gian PEAK NHẤT & CUỐN HÚT NHẤT (`peak_offset_sec`).
       - Trích xuất 3 khung hình NGAY TẠI ĐOẠN PEAK (`-ss peak_offset_sec`, không lấy ở giây `0.0s` khi video gốc chưa vào cao trào)
         để Gemini Vision chấm điểm và trả về `"recommended_hook_mode": "video" | "scene"`.
    4. Cắt & ghép đoạn Peak đạt chuẩn vào ĐẦU VIDEO (Cảnh 1) và vào TRONG THÂN VIDEO (`climax_scene_id`).
    """
    from app.utils.key_rotator import KeyRotator

    work_dir = Path(state.get("work_dir", "storage/cache"))
    work_dir.mkdir(parents=True, exist_ok=True)
    hook_cand_dir = work_dir / "hook_candidates"
    hook_cand_dir.mkdir(parents=True, exist_ok=True)

    script_data = state.get("script_data") or {}
    script_scenes = list(script_data.get("scenes") or [])
    scenes_media = list(state.get("scenes_media") or [])
    scenes_audio = list(state.get("scenes_audio") or [])

    base_scenes = scenes_media if scenes_media else script_scenes
    if not base_scenes:
        return {"current_step": "HOOK_VIDEO_SKIPPED", "pre_resolved_hook_media": {}}

    scene_1 = dict(base_scenes[0]) if isinstance(base_scenes[0], dict) else {}
    audio_1 = next((s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id") == 1), {})

    topic = state.get("topic") or script_data.get("topic") or ""
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity") or ""
    niche = state.get("channel_style") or state.get("niche") or "general"
    visual_keyword = scene_1.get("visual_keyword") or topic
    hook_narration = scene_1.get("narration") or audio_1.get("narration") or script_data.get("hook") or ""

    dur_ms = (
        audio_1.get("audio_duration_ms")
        or audio_1.get("duration_ms")
        or scene_1.get("audio_duration_ms")
        or int(max(float(scene_1.get("estimated_seconds") or 3.5), 1.0) * 1000)
    )
    transition_duration = 0.18 if len(base_scenes) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
    target_hook_duration_sec = max(dur_ms / 1000.0, 1.0) + transition_duration

    keys = settings.get_gemini_keys_pool() if hasattr(settings, "get_gemini_keys_pool") else []
    if not keys:
        raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 1: CỔNG PHÂN LOẠI HOOK CỦA GEMINI NGAY TRONG HÀM TẠO HOOK
    # Gemini trả về trực tiếp:
    # - `hook_visual_mode`: "video" (tìm & cắt đoạn Peak từ Short) hay "scene" (tạo cảnh / tìm ảnh truyền thống)
    # - `is_abstract_keyword`: True / False
    # - `climax_scene_id`: Cảnh cao trào trong thân video (2..N) để ghép đoạn Peak vào thân video
    # - `search_queries`: 2 câu truy vấn tìm YouTube Shorts
    # ──────────────────────────────────────────────────────────────────────────
    fallback_queries = _build_hook_video_search_queries(topic, primary_entity, visual_keyword, niche)
    default_climax_scene_id = 3 if len(base_scenes) >= 4 else (2 if len(base_scenes) >= 2 else 1)

    full_script_text = " | ".join(
        f"Scene {s.get('scene_id', i + 1)}: {s.get('narration', '')} ({s.get('visual_keyword', '')})"
        for i, s in enumerate(base_scenes[:9])
        if isinstance(s, dict)
    )

    # Kiểm tra xem khi tạo kịch bản/hook ở ContentAgent, Gemini đã trả về sẵn quyết định chưa;
    # đồng thời gọi Gemini ngay trong hàm tạo Hook để phân tích chính xác kịch bản & quyết định "video" vs "scene".
    pre_mode = str(script_data.get("hook_visual_mode") or "").strip().lower()
    pre_abstract = script_data.get("is_abstract_keyword")
    pre_climax_id = script_data.get("climax_scene_id")
    pre_queries = script_data.get("hook_search_queries")

    hook_visual_mode: str = "scene" if pre_mode == "scene" else "video"
    is_abstract_keyword: bool = bool(pre_abstract) if isinstance(pre_abstract, bool) else (hook_visual_mode == "scene")
    climax_scene_id: int = int(pre_climax_id) if isinstance(pre_climax_id, int) and 1 < pre_climax_id <= len(base_scenes) else default_climax_scene_id
    queries: List[str] = (
        [str(q).strip() for q in pre_queries if str(q).strip()][:2]
        if isinstance(pre_queries, list) and pre_queries
        else fallback_queries
    )
    climax_desc: str = f"{primary_entity or topic} — {visual_keyword}"
    atmosphere_fx: str = "action"

    plan_prompt = f"""You are an elite Viral YouTube Shorts Hook Director.
Analyze this video's Hook and full script, and decide whether the Hook should be produced as a real cut Short VIDEO ("video") or generated/searched as a visual SCENE ("scene").

- Topic: {topic}
- Primary Subject: {primary_entity or topic}
- Channel Niche: {niche}
- Opening Hook Narration (Scene 1): {hook_narration}
- Scene 1 Visual Keyword: {visual_keyword}
- Full Script Summary: {full_script_text[:1400]}

Return a JSON object with your decision:
1. "canonical_subject": Bare 1-2 word canonical noun of the main creature/animal/subject WITHOUT literary or size adjectives (e.g. "leviathan", "kraken", "honey badger", "black hole"; NEVER include words like "colossal", "abyssal", "terrifying", "ancient").
2. "hook_visual_mode": Must be strictly "video" or "scene":
   - Choose "video" when the Hook/Climax depicts a concrete physical subject, wildlife predator, or 3D CGI/VFX monster/sci-fi creature where cutting the Peak moment from a YouTube Short creates an explosive opening.
   - Choose "scene" when the Hook/Keyword is abstract, psychological, conceptual, or counterfactual.
3. "is_abstract_keyword": Boolean (true if "hook_visual_mode" is "scene", false if "hook_visual_mode" is "video").
4. "climax_scene_id": Integer scene_id (between 2 and {max(len(base_scenes), 2)}) where the most intense mid-video climax occurs in the body of the script so we can splice the Peak clip there too.
5. "climax_description": 1-sentence description of the peak visual climax.
6. "search_queries": 3 high-hit English YouTube Shorts search queries (2 to 4 words each, strictly using "canonical_subject" + action/format, NO quotes, NO hashtags, NO flowery adjectives):
   - If "canonical_subject" is a REAL living animal, real person, or real-world phenomenon (e.g. orca, great white shark, jaguar, honey badger, harpy eagle, submarine, diver): NEVER use "3d animation" or "size comparison"! Always query real wildlife/documentary action (e.g. "orca hunting shark", "jaguar attacking caiman").
   - ONLY if "canonical_subject" is a fictional, mythical, or extinct monster/cryptid with zero real camera footage (e.g. leviathan, kraken, bloop, cthulhu, megalodon): include "<canonical_subject> 3d animation" and "<canonical_subject> thalassophobia".
7. "atmosphere_fx": Pick ONE from: "history", "psychology", "mystery", "action".

Return ONLY valid JSON:
{{
  "canonical_subject": "subject noun",
  "hook_visual_mode": "video",
  "is_abstract_keyword": false,
  "climax_scene_id": {climax_scene_id},
  "climax_description": "...",
  "search_queries": ["query 1", "query 2", "query 3"],
  "atmosphere_fx": "action"
}}"""

    rotator = KeyRotator(keys)
    gemini_decided = False
    canonical_subject = ""
    for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=3):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": plan_prompt}]}]}, timeout=7)
            if resp.status_code == 200:
                cands = resp.json().get("candidates", [])
                if cands:
                    txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                    m = re.search(r"\{.*\}", txt, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        canonical_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(data.get("canonical_subject") or "")).strip()
                        mode_raw = str(data.get("hook_visual_mode") or "").strip().lower()
                        abs_raw = bool(data.get("is_abstract_keyword", mode_raw == "scene"))
                        if mode_raw in ("scene", "image", "ai_image", "photo") or abs_raw:
                            hook_visual_mode = "scene"
                            is_abstract_keyword = True
                        else:
                            hook_visual_mode = "video"
                            is_abstract_keyword = False

                        raw_q = [
                            re.sub(r"[^a-zA-Z0-9\s]", " ", str(q)).strip()
                            for q in (data.get("search_queries") or [])
                            if str(q).strip()
                        ]
                        valid_q = [re.sub(r"\s+", " ", q) for q in raw_q if len(q.split()) >= 2][:3]
                        if valid_q:
                            queries = valid_q

                        c_id_val = int(data.get("climax_scene_id") or climax_scene_id)
                        if 1 < c_id_val <= len(base_scenes):
                            climax_scene_id = c_id_val

                        climax_desc = str(data.get("climax_description") or climax_desc).strip()
                        atmosphere_fx = str(data.get("atmosphere_fx") or niche or "action").lower()
                        gemini_decided = True
                        break
            elif resp.status_code in (429, 403, 404, 503):
                rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
        except Exception as e:
            rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e))
            logger.debug(f"[HookVideoNode] Gemini Hook Mode classifier fallback ({model_name}): {e}")

    if not gemini_decided and not pre_mode:
        combined_text = f"{topic} {hook_narration} {visual_keyword} {niche}".lower()
        if any(k in combined_text for k in ("psych", "manipulat", "gaslight", "narcissist", "love bomb", "mindset", "what if", "alternate history")):
            hook_visual_mode = "scene"
            is_abstract_keyword = True
        else:
            hook_visual_mode = "video"
            is_abstract_keyword = False

    pre_resolved_hook_media: Dict[int, Dict[str, Any]] = {}

    # BẮT BUỘC 100% CẢNH 1 (HOOK) TRÊN MỌI KÊNH (KỂ CẢ PSYCHOLOGY, ALTERNATE HISTORY, MYSTERIES) PHẢI LÀ VIDEO CHUYỂN ĐỘNG THẬT!
    # Tuyệt đối không bỏ qua bước săn Video Mở Đầu để tránh lỗi mở màn bằng ảnh tĩnh gây tụt Stay Rate.
    if hook_visual_mode == "scene" or is_abstract_keyword:
        logger.info(
            f"🔥 [HookVideoNode] Kênh '{niche}' có từ khóa trừu tượng ('{visual_keyword}') "
            f"-> Ép chế độ `hook_visual_mode='video'` (săn Short / Stock Video chuyển động thật cho Cảnh 1 thay vì ảnh tĩnh)!"
        )
        hook_visual_mode = "video"
        is_abstract_keyword = False

    # Nhận diện chủ đề Quái vật khổng lồ / Thủy quái biển sâu / Khoa học viễn tưởng (Leviathan, Bloop, Kraken, Megalodon...)
    # để tự động bổ sung truy vấn 3D CGI / Thalassophobia trên YouTube Shorts thay vì tìm đạo cụ "sonar screen"
    _hook_ctx_low = f"{topic} {primary_entity} {visual_keyword} {hook_narration}".lower()
    is_monster_hook = any(
        mk in _hook_ctx_low
        for mk in (
            "leviathan",
            "bloop",
            "kraken",
            "megalodon",
            "cthulhu",
            "kaiju",
            "godzilla",
            "titanoboa",
            "mosasaurus",
            "sea monster",
            "colossal monster",
            "thalassophobia",
            "megalophobia",
        )
    )
    if is_monster_hook:
        from app.graph.tools.media_tools import _clean_yt_search_phrase
        clean_m_subj = (
            _clean_yt_search_phrase(canonical_subject or primary_entity or topic, max_words=2, strip_distractor_props=True)
            or "sea monster"
        )
        monster_hook_queries = [
            *queries,
            f"{clean_m_subj} 3d animation",
            "sea monster thalassophobia",
            f"{clean_m_subj} megalophobia",
        ]
        queries = [q for q in dict.fromkeys(monster_hook_queries) if q][:3]

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 2: GEMINI CHỌN "video" -> SĂN VIDEO SHORT TRÊN MẠNG (CHỐNG LẶP 100%)
    # ──────────────────────────────────────────────────────────────────────────
    logger.info(
        f"🎯 [HookVideoNode] Gemini chọn `hook_visual_mode='video'` -> Săn tìm Short trên mạng cho Cao Trào: "
        f"'{climax_desc[:75]}' | Queries: {queries} | Hook={target_hook_duration_sec:.2f}s | ClimaxScene=#{climax_scene_id}"
    )

    from app.services.video_memory_rag import get_video_memory_rag
    rag = get_video_memory_rag()
    job_id = int(state.get("job_id") or 0)
    channel_id = state.get("target_channel_id") or state.get("channel_id")
    rag.reset_job_session(job_id)

    existing_path = str(scene_1.get("media_path") or "")
    candidate_videos: List[str] = []
    candidate_titles: Dict[str, str] = {}
    candidate_uploaders: Dict[str, str] = {}
    if (
        existing_path
        and existing_path.lower().endswith((".mp4", ".mov", ".webm"))
        and Path(existing_path).exists()
    ):
        candidate_videos.append(existing_path)

    from app.graph.tools.media_tools import (
        _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST,
        _TALKING_HEAD_BLOCKLIST,
    )
    from app.graph.evaluators.visual_critic import VisualCritic

    for q_idx, q in enumerate(queries):
        if len(candidate_videos) >= 2:
            break
        # Quét danh sách ứng viên qua yt_dlp flat search, chặn kênh rủi ro bản quyền/watermark và xếp hạng bằng Mini-RAG
        selected_urls: List[str] = []
        try:
            import yt_dlp
            flat_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "skip_download": True,
                "socket_timeout": 8,
                "ignoreerrors": True,
            }
            with yt_dlp.YoutubeDL(license_policy.youtube_search_opts(flat_opts, max_results=15)) as ydl_flat:
                flat_res = ydl_flat.extract_info(license_policy.youtube_search_target(q, max_results=15), download=False)
            raw_flat_cands: List[Dict[str, Any]] = []
            for entry in ((flat_res or {}).get("entries") or []):
                if not isinstance(entry, dict):
                    continue
                vid_id = str(entry.get("id") or "").strip()
                if not vid_id:
                    continue
                dur_e = float(entry.get("duration") or 0)
                if dur_e > 0 and (dur_e < 4 or dur_e > 180):
                    continue
                title_e = str(entry.get("title") or "")
                uploader_e = str(entry.get("uploader") or entry.get("channel") or "")
                meta_e = f"{title_e} {uploader_e}".lower()
                if any(bad in meta_e for bad in _TALKING_HEAD_BLOCKLIST) or any(
                    bad_c in meta_e for bad_c in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST
                ):
                    continue
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
                views_e = int(entry.get("view_count") or 0)
                raw_flat_cands.append({
                    "id": vid_id,
                    "url": vid_url,
                    "title": title_e,
                    "uploader": uploader_e,
                    "duration": dur_e,
                    "score": min(views_e / 25000.0, 60.0),
                })

            ranked_flat = rag.rank_video_candidates_for_scene(
                job_id=job_id,
                candidates=raw_flat_cands,
                scene_narration=hook_narration,
                scene_query=q,
                topic=topic,
                subject_entity=primary_entity or climax_desc,
                channel_id=channel_id,
            )
            for rc in ranked_flat[:2]:
                selected_urls.append(rc["url"])
                candidate_titles[rc["id"]] = rc.get("title", "")
                candidate_uploaders[rc["id"]] = rc.get("uploader", "")
        except Exception as e_flat:
            logger.debug(f"[HookVideoNode] yt_dlp flat pre-filter skip ({q}): {e_flat}")

        if not selected_urls:
            continue

        out_tmpl = str(hook_cand_dir / f"yt_hook_cand_{q_idx}_%(id)s.%(ext)s")
        cmd_yt = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout", "8",
            "--max-filesize", "28M",
            "--match-filter", license_policy.ytdlp_cli_match_filter("duration <= 180"),
            "--max-downloads", "2",
            "-f", "bestvideo[height>=720][height<=1920][ext=mp4]/best[height>=720][height<=1920][ext=mp4]",
            "-o", out_tmpl,
            *selected_urls,
        ]
        try:
            subprocess.run(
                cmd_yt,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except Exception as e_yt:
            logger.debug(f"[HookVideoNode] yt-dlp search skip ({q}): {e_yt}")

        for mp4 in sorted(hook_cand_dir.glob(f"yt_hook_cand_{q_idx}_*.mp4")):
            if mp4.stat().st_size > 80_000 and str(mp4) not in candidate_videos:
                gate_res = VisualCritic.inspect_clip_before_concat(
                    str(mp4), min_short_edge=720, min_sharpness=5.8, check_watermark=True
                )
                if not gate_res.get("passed", False):
                    logger.info(
                        f"🚫 [HookVideoNode Gate] Loại bỏ ứng viên Hook '{mp4.name}' vì: "
                        f"{'; '.join(gate_res.get('issues') or [])}"
                    )
                    mp4.unlink(missing_ok=True)
                    continue
                candidate_videos.append(str(mp4))
                if len(candidate_videos) >= 2:
                    break

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 3: QUÉT ĐOẠN PEAK TỰ NHIÊN (`peak_offset_sec`, `peak_duration_sec`) & CHẤM ĐIỂM VISION
    # (Gỡ khóa cứng 3s: Đo luôn cả độ dài thực sự của đoạn Peak tới 15s nếu cao trào kéo dài!)
    # ──────────────────────────────────────────────────────────────────────────
    scored_candidates: List[Dict[str, Any]] = []
    passing_candidates: List[Dict[str, Any]] = []
    best_candidate: Optional[Dict[str, Any]] = None

    for idx, v_path in enumerate(candidate_videos):
        # 3.1. Quét toàn bộ video Short để tìm đúng mốc Peak & đo trọn vẹn độ dài đoạn Peak tự nhiên (tới 15s)
        peak_offset_sec, peak_duration_sec = find_hook_peak_window(
            v_path,
            min_duration=target_hook_duration_sec,
            max_duration=15.0,
        )

        # 3.2. Trích xuất 3 khung hình đại diện NGAY TẠI ĐOẠN PEAK (`-ss peak_offset_sec`)
        sheet_path = hook_cand_dir / f"hook_score_sheet_{idx}.jpg"
        win_dur = max(peak_duration_sec, target_hook_duration_sec, 2.4)
        fps_expr = f"3/{win_dur:.2f}"
        seek_args = ["-ss", f"{peak_offset_sec:.2f}"] if peak_offset_sec >= 0.15 else []
        cmd_sheet = [
            "ffmpeg", "-y",
            *seek_args,
            "-t", f"{win_dur:.2f}",
            "-i", v_path,
            "-vf", f"fps={fps_expr},scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
            "-frames:v", "1",
            "-q:v", "4",
            str(sheet_path),
        ]
        b64_sheet: Optional[str] = None
        try:
            subprocess.run(cmd_sheet, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
            if sheet_path.exists() and sheet_path.stat().st_size > 5_000:
                b64_sheet = base64.b64encode(sheet_path.read_bytes()).decode("ascii")
        except Exception as e_sh:
            logger.debug(f"[HookVideoNode] Lỗi trích xuất 3 khung hình Peak cho {v_path}: {e_sh}")

        if not b64_sheet:
            continue

        # 3.3. Gửi 3 khung hình tại đoạn Peak cho Gemini Vision chấm điểm & quyết định nên dùng "video" hay "scene"
        vision_prompt = f"""You are a strict YouTube Shorts Hook Video Quality Grader & Director.
Evaluate this 3-frame sequence extracted directly from the PEAK CLIMAX window (starting at {peak_offset_sec:.1f}s, natural peak length {peak_duration_sec:.1f}s) of a candidate Short video for:
- Video Topic: "{topic} (Climax: {climax_desc})"
- Primary Subject/Entity: "{primary_entity or climax_desc}"
- Opening Hook Line: "{hook_narration}"

Score strictly from 1.0 to 10.0 and decide whether this Hook should use this cut Peak VIDEO ("video") or fall back to creating a traditional visual SCENE ("scene"):
1. "recommended_hook_mode": Return "video" if these 3 peak frames show compelling, relevant real motion/footage worthy of splicing into the Hook and Climax. Return "scene" if the footage is unrelated, a talking head, or cluttered with giant text.
2. "entity_match_score": Does the peak clip visually match the subject/entity or intriguing climax ("{primary_entity or climax_desc}")? (10 = exact or compelling visual match, 1 = unrelated).
3. "motion_action_score": Is it dynamic real footage/action/intriguing visual rather than a talking head / podcast / static slide? (10 = intense real action or intriguing motion, 1 = talking head or static slide).
4. "clean_frame_score": Is the center of the visual reasonably clean without giant burned-in text blocking the subject? (10 = clean footage, 1 = cluttered with huge text).
5. "reason": Short 1-sentence explanation.

Return ONLY valid JSON:
{{"recommended_hook_mode": "video", "entity_match_score": 8.5, "motion_action_score": 9.0, "clean_frame_score": 8.0, "reason": "..."}}"""

        score_res: Optional[Dict[str, Any]] = None
        for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=3):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": vision_prompt},
                                {"inline_data": {"mime_type": "image/jpeg", "data": b64_sheet}},
                            ]
                        }
                    ]
                }
                resp = requests.post(url, json=payload, timeout=7)
                if resp.status_code == 200:
                    cands = resp.json().get("candidates", [])
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            rec_mode = str(data.get("recommended_hook_mode") or "video").strip().lower()
                            e_score = float(data.get("entity_match_score", 5.0))
                            m_score = float(data.get("motion_action_score", 5.0))
                            c_score = float(data.get("clean_frame_score", 5.0))
                            overall = round(e_score * 0.45 + m_score * 0.35 + c_score * 0.20, 2)
                            passed = (
                                rec_mode == "video"
                                and (overall >= 6.8)
                                and (e_score >= 6.5)
                                and (c_score >= 6.0)
                                and (m_score >= 6.5)
                            )
                            score_res = {
                                "video_path": v_path,
                                "peak_offset_sec": peak_offset_sec,
                                "peak_duration_sec": peak_duration_sec,
                                "recommended_hook_mode": "video" if passed else "scene",
                                "entity_match_score": e_score,
                                "motion_action_score": m_score,
                                "clean_frame_score": c_score,
                                "overall_score": overall,
                                "passed": passed,
                                "reason": data.get("reason", ""),
                            }
                            break
                elif resp.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
            except Exception as e_vis:
                rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e_vis))
                logger.debug(f"[HookVideoNode] Gemini Vision Peak score error ({model_name}): {e_vis}")

        # Fallback Computer Vision cục bộ ngay tại đoạn Peak (`peak_offset_sec`) nếu Gemini API chạm 429
        if score_res is None:
            try:
                from PIL import Image, ImageChops, ImageFilter, ImageStat

                w, h = 180, 320
                frame_bytes = w * h * 3
                cmd_cv = [
                    "ffmpeg", "-v", "quiet",
                    *seek_args,
                    "-i", v_path,
                    "-t", "5",
                    "-vf", f"fps=2,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    "-f", "rawvideo",
                    "-pix_fmt", "rgb24",
                    "-",
                ]
                res_cv = subprocess.run(cmd_cv, capture_output=True, timeout=8, check=False)
                raw = res_cv.stdout or b""
                n_frames = len(raw) // frame_bytes
                if n_frames >= 3:
                    motions, contrasts, sharpness_vals = [], [], []
                    prev_g = None
                    for i_f in range(n_frames):
                        im = Image.frombytes("RGB", (w, h), raw[i_f * frame_bytes : (i_f + 1) * frame_bytes])
                        g = im.convert("L")
                        contrasts.append(float(ImageStat.Stat(g).stddev[0]))
                        sharpness_vals.append(float(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]))
                        if prev_g is not None:
                            motions.append(float(ImageStat.Stat(ImageChops.difference(g, prev_g)).mean[0]))
                        prev_g = g
                    avg_motion = sum(motions) / max(len(motions), 1)
                    avg_contrast = sum(contrasts) / max(len(contrasts), 1)
                    avg_sharp = sum(sharpness_vals) / max(len(sharpness_vals), 1)
                    e_score = 8.0
                    m_score = round(min(9.5, max(4.0, 5.5 + avg_motion * 0.22)), 2)
                    c_score = round(min(9.2, max(5.0, 5.5 + (avg_contrast / 18.0) + (avg_sharp / 15.0))), 2)
                    overall = round(e_score * 0.45 + m_score * 0.35 + c_score * 0.20, 2)
                    passed = (avg_motion >= 3.0) and (avg_contrast >= 24.0) and (overall >= 6.8)
                    score_res = {
                        "video_path": v_path,
                        "peak_offset_sec": peak_offset_sec,
                        "peak_duration_sec": peak_duration_sec,
                        "recommended_hook_mode": "video" if passed else "scene",
                        "entity_match_score": e_score,
                        "motion_action_score": m_score,
                        "clean_frame_score": c_score,
                        "overall_score": overall,
                        "passed": passed,
                        "reason": f"Local CV Peak Grader @{peak_offset_sec:.1f}s (len={peak_duration_sec:.1f}s, Motion={avg_motion:.1f})",
                    }
            except Exception as e_cv:
                logger.debug(f"[HookVideoNode] Local CV fallback error: {e_cv}")

        if score_res is None:
            continue

        scored_candidates.append(score_res)
        logger.info(
            f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name} @Peak {peak_offset_sec:.1f}s, len={peak_duration_sec:.1f}s): "
            f"Mode={score_res['recommended_hook_mode']} | Overall={score_res['overall_score']}/10 "
            f"(Entity={score_res['entity_match_score']}, Motion={score_res['motion_action_score']}, Clean={score_res['clean_frame_score']}) "
            f"-> {'✅ ĐẠT (Dùng Video Peak)' if score_res['passed'] else '❌ LOẠI (Chuyển sang Tạo Cảnh)'} ({score_res['reason']})"
        )
        if score_res["passed"]:
            passing_candidates.append(score_res)
            if best_candidate is None or score_res["overall_score"] > best_candidate["overall_score"]:
                best_candidate = score_res

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 4: CẮT ĐOẠN PEAK GHÉP VÀO ĐẦU VIDEO + PHỦ LIỀN MẠCH NẾU ĐOẠN PEAK DÀI
    # (Gỡ khóa cứng 3s: Nếu đoạn Peak kéo dài 7s-15s, tự động nối liền qua Cảnh 1 -> Cảnh 2 -> Cảnh 3!)
    # ──────────────────────────────────────────────────────────────────────────
    hook_output_clip = str(work_dir / "hook_motion_001.mp4")
    is_scifi = "scifi" in atmosphere_fx or "scifi" in niche.lower()

    def _get_scene_dur_sec(sid: int, default_s: float = 3.5) -> float:
        sc_a = next((s for s in scenes_audio if isinstance(s, dict) and int(s.get("scene_id") or 0) == sid), {})
        ms = sc_a.get("audio_duration_ms") or sc_a.get("duration_ms") or 0
        if ms:
            return max(float(ms) / 1000.0, 1.4)
        if 0 < sid <= len(base_scenes):
            b_sc = base_scenes[sid - 1]
            est = b_sc.get("estimated_seconds") if isinstance(b_sc, dict) else getattr(b_sc, "estimated_seconds", None)
            if est:
                return max(float(est), 1.4)
        return default_s

    if best_candidate and Path(best_candidate["video_path"]).exists():
        best_v_path = best_candidate["video_path"]
        peak_start = float(best_candidate.get("peak_offset_sec", 0.0))
        natural_peak_dur = float(best_candidate.get("peak_duration_sec") or target_hook_duration_sec)
        m_yt_hook = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(best_v_path).name)
        hook_vid_id = m_yt_hook.group(1) if m_yt_hook else ""
        hook_vid_title = candidate_titles.get(hook_vid_id, "")

        logger.info(
            f"🔥 [HookVideoNode] Đã cắt đoạn PEAK NHẤT (@{peak_start:.2f}s, độ dài tự nhiên={natural_peak_dur:.2f}s, "
            f"Score={best_candidate['overall_score']}/10) ghép vào ĐẦU VIDEO (Cảnh 1) -> {hook_output_clip}"
        )
        trim_video(
            input_path=best_v_path,
            duration_seconds=target_hook_duration_sec,
            output_path=hook_output_clip,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=atmosphere_fx,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=True,
            start_offset_sec=peak_start,
            preserve_continuous_flow=True,
        )
        hook_mode = "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK"
        final_hook_visual_mode = "video"
        if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
            # Đăng ký Cảnh 1 vào bộ nhớ RAG nội bộ của Job (chống các cảnh sau lấy trùng video hoặc bản re-upload của Cảnh 1)
            rag.verify_and_commit_in_job_clip(
                job_id=job_id,
                scene_id=1,
                media_path=hook_output_clip,
                vid_id=hook_vid_id,
                url=best_v_path,
                title=hook_vid_title,
                is_continuation_slice=False,
            )
            if hook_vid_id and license_policy.cc_only_enabled():
                license_policy.record_credit(
                    job_id=job_id,
                    video_id=hook_vid_id,
                    title=hook_vid_title,
                    uploader=candidate_uploaders.get(hook_vid_id, ""),
                )
            pre_resolved_hook_media[1] = {
                "media_path": hook_output_clip,
                "media_type": "hook_ready_video",
                "production_method": hook_mode,
                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                "strategy_rationale": f"Peak Short clip @{peak_start:.1f}s (full_peak={natural_peak_dur:.1f}s, {best_candidate['overall_score']}/10)",
            }
            if scenes_media:
                scene_1["media_path"] = hook_output_clip
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = hook_mode
                scenes_media[0] = scene_1

        # ── MỞ RỘNG ĐOẠN PEAK DÀI LIỀN MẠCH QUA CÁC CẢNH KẾ TIẾP (CẢNH 2, CẢNH 3...) ──
        # Nếu đoạn Peak thực tế dài (ví dụ 7s - 15s), không ngắt cụt ở 3s mà nối tiếp mạch chuyển động sang Cảnh 2, Cảnh 3!
        # Không cắt nối đoạn Peak của Cảnh 1 sang Cảnh 2..4 để giữ đúng khớp lời đọc từng phân cảnh.

        # Ghép thêm đoạn Peak thứ 2 vào TRONG THÂN VIDEO (tại Cảnh Cao Trào `climax_scene_id`)
        # TUYỆT ĐỐI KHÔNG cắt lại cùng 1 video của Cảnh 1 (`best_v_path`) cho một cảnh rời rạc phía sau!
        # Chỉ ghép nếu có một video ứng viên ĐỘC LẬP thứ 2 (`other_passing`) vượt qua kiểm duyệt chống trùng lặp của Mini-RAG.
        if 1 < climax_scene_id <= len(base_scenes) and climax_scene_id not in pre_resolved_hook_media:
            other_passing = [c for c in passing_candidates if c["video_path"] != best_v_path]
            for sec_cand in other_passing:
                second_v_path = sec_cand["video_path"]
                m_yt_sec = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(second_v_path).name)
                sec_vid_id = m_yt_sec.group(1) if m_yt_sec else ""
                sec_title = candidate_titles.get(sec_vid_id, "")
                if not rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=sec_vid_id,
                    url=second_v_path,
                    title=sec_title,
                    scene_id=climax_scene_id,
                ):
                    continue

                climax_dur_sec = _get_scene_dur_sec(climax_scene_id, default_s=3.5) + 0.18
                climax_output_clip = str(work_dir / f"hook_climax_scene_{climax_scene_id:03d}.mp4")
                second_peak_start = float(sec_cand.get("peak_offset_sec", 0.0))
                second_peak_dur = float(sec_cand.get("peak_duration_sec") or climax_dur_sec)

                try:
                    trim_video(
                        input_path=second_v_path,
                        duration_seconds=climax_dur_sec,
                        output_path=climax_output_clip,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=atmosphere_fx,
                        apply_grading=True,
                        is_hook_scene=False,
                        fair_use_transform=True,
                        start_offset_sec=second_peak_start,
                        preserve_continuous_flow=True,
                    )
                    if Path(climax_output_clip).exists() and Path(climax_output_clip).stat().st_size > 10_000:
                        ok_climax, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=climax_scene_id,
                            media_path=climax_output_clip,
                            vid_id=sec_vid_id,
                            url=second_v_path,
                            title=sec_title,
                            is_continuation_slice=False,
                        )
                        if not ok_climax:
                            try:
                                Path(climax_output_clip).unlink(missing_ok=True)
                            except Exception:
                                pass
                            continue

                        if sec_vid_id and license_policy.cc_only_enabled():
                            license_policy.record_credit(
                                job_id=job_id,
                                video_id=sec_vid_id,
                                title=sec_title,
                                uploader=candidate_uploaders.get(sec_vid_id, ""),
                            )
                        pre_resolved_hook_media[climax_scene_id] = {
                            "media_path": climax_output_clip,
                            "media_type": "hook_ready_video",
                            "production_method": "CLIMAX_PEAK_SHORT_SPLICE",
                            "visual_strategy": "STOCK_VIDEO",
                            "strategy_rationale": f"Mid-video distinct Peak Short @{second_peak_start:.1f}s (len={second_peak_dur:.1f}s)",
                        }
                        logger.info(
                            f"🔥 [HookVideoNode] Đã cắt video Peak độc lập thứ 2 (@{second_peak_start:.2f}s, len={second_peak_dur:.1f}s) "
                            f"ghép vào Cảnh #{climax_scene_id} -> {climax_output_clip}"
                        )
                        break
                except Exception as e_climax:
                    logger.debug(f"[HookVideoNode] Không thể cắt đoạn Peak thứ 2 cho Cảnh #{climax_scene_id}: {e_climax}")
    else:
        # CỨU HỘ CẢNH 1 BẰNG VIDEO CHUYỂN ĐỘNG THẬT TỪ PEXELS / PIXABAY HD TRƯỚC KHI FALLBACK VỀ ẢNH TĨNH
        stock_hook_ok = False
        try:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            st_q = queries[0] if queries else (visual_keyword or topic)
            st_res = execute_youtube_short_peak_clone_sync(
                search_query=st_q,
                output_path=hook_output_clip,
                target_duration_sec=target_hook_duration_sec,
                fallback_query=queries[1] if len(queries) > 1 else topic,
                clip_role="HOOK",
                subject_entity=primary_entity or topic,
                topic=topic,
                narration=hook_narration,
                search_scope="SCRIPT_OVERALL",
                overall_script_query=st_q,
                job_id=job_id,
                scene_id=1,
                channel_id=channel_id,
                allow_3d_cgi_monster=is_monster_hook,
            )
            if st_res and st_res.get("media_path") and Path(st_res["media_path"]).exists():
                stock_hook_ok = True
                final_hook_visual_mode = "video"
                hook_mode = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                pre_resolved_hook_media[1] = {
                    "media_path": st_res["media_path"],
                    "media_type": "hook_ready_video",
                    "production_method": "CLIMAX_STOCK_VIDEO_REAL_MOTION",
                    "visual_strategy": "STOCK_VIDEO",
                    "strategy_rationale": f"Real motion Hook video ({st_res.get('title', 'Stock HD')})",
                }
                scene_1["media_path"] = st_res["media_path"]
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                if scenes_media:
                    scenes_media[0] = scene_1
                logger.info(f"🎬 [HookVideoNode] Đã nạp Video chuyển động thật cho Cảnh 1: {st_res['media_path']}")
        except Exception as e_st_hk:
            logger.debug(f"[HookVideoNode] Stock video hook rescue skip: {e_st_hk}")

        if not stock_hook_ok:
            final_hook_visual_mode = "scene"
            pose_list = scene_1.get("pose_images") or ([existing_path] if existing_path and Path(existing_path).exists() else [])
            if pose_list:
                logger.info(
                    f"⚡ [HookVideoNode] Chuyển sang chế độ Tạo Cảnh (Scene) -> Dùng {len(pose_list)} ảnh dựng 3-Cut Dynamic Hook -> {hook_output_clip}"
                )
                render_opening_hook_multi_cut(
                    image_paths=pose_list,
                    duration_seconds=target_hook_duration_sec,
                    output_path=hook_output_clip,
                    width=1080,
                    height=1920,
                    fps=30,
                    min_cuts=3,
                    is_scifi=is_scifi,
                    niche=atmosphere_fx,
                )
                if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
                    scene_1["media_path"] = hook_output_clip
                    scene_1["media_type"] = "hook_ready_video"
                    scene_1["production_method"] = f"CLIMAX_IMAGES_LIVE_FX_{atmosphere_fx.upper()}_3CUT"
                    if scenes_media:
                        scenes_media[0] = scene_1
            else:
                logger.info(
                    "⚡ [HookVideoNode] Các ứng viên Short không đạt chuẩn -> Gemini chuyển Hook sang chế độ Tạo Cảnh ('scene') tại fetch_media!"
                )
            hook_mode = f"TRADITIONAL_SCENE_FALLBACK_{atmosphere_fx.upper()}"

    eval_summary = {
        "mode": hook_mode,
        "hook_visual_mode": final_hook_visual_mode,
        "is_abstract_keyword": is_abstract_keyword,
        "climax_scene_id": climax_scene_id,
        "climax_description": climax_desc,
        "search_queries": queries,
        "atmosphere_fx": atmosphere_fx,
        "candidates_tested": len(scored_candidates),
        "best_score": best_candidate["overall_score"] if best_candidate else 8.5,
        "best_reason": best_candidate["reason"] if best_candidate else "Fallback to traditional scene/image creation",
        "output_clip": hook_output_clip if Path(hook_output_clip).exists() else None,
        "spliced_scenes": list(pre_resolved_hook_media.keys()),
        "duration_sec": round(target_hook_duration_sec, 2),
    }

    out_state: Dict[str, Any] = {
        "pre_resolved_hook_media": pre_resolved_hook_media,
        "hook_video_eval": eval_summary,
        "current_step": "HOOK_VIDEO_READY",
        "progress_percent": 58,
        "progress_message": (
            f"Đã xử lý Hook (Gemini mode='{final_hook_visual_mode}', "
            f"ghép vào cảnh: {list(pre_resolved_hook_media.keys()) or ['Tạo cảnh truyền thống']})"
        ),
    }
    if scenes_media:
        out_state["scenes_media"] = scenes_media
    return out_state
