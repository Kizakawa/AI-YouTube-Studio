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
    full_script_text: str = "",
) -> List[str]:
    """Tạo danh sách truy vấn dự phòng ngắn gọn (2-5 từ) bám sát hành động vật lý thực tế của toàn bộ kịch bản."""
    queries: List[str] = []
    clean_kw = re.sub(r"[^a-zA-Z0-9\s]", " ", visual_keyword or "").strip()
    clean_ent = re.sub(r"[^a-zA-Z0-9\s]", " ", primary_entity or "").strip()
    clean_top = re.sub(r"[^a-zA-Z0-9\s]", " ", topic or "").strip()

    stop_words = {
        "cinematic", "photorealistic", "dramatic", "lighting", "4k", "8k", "vertical",
        "why", "how", "what", "when", "secret", "behind", "truth", "about", "never",
        "psychology", "manipulation", "narcissist", "gaslighting", "mindset", "stoicism",
    }
    kw_tokens = [w for w in clean_kw.split() if w.lower() not in stop_words][:4]
    ent_tokens = [w for w in clean_ent.split() if w.lower() not in stop_words][:3]
    top_tokens = [w for w in clean_top.split() if w.lower() not in stop_words][:4]

    niche_l = (niche or "").lower()
    comb_l = f"{topic} {visual_keyword} {full_script_text[:400]}".lower()
    if "psych" in niche_l or any(k in comb_l for k in ("interrogat", "liar", "body language", "fake smile", "eye contact")):
        if "feet" in comb_l or "foot" in comb_l or "table" in comb_l:
            queries.extend(["nervous hands interrogation dark", "man intense eye contact shadow"])
        elif "smile" in comb_l or "envy" in comb_l:
            queries.extend(["woman cold fake smile dark", "suspicious glance side eye cinematic"])
        else:
            queries.extend(["intense eye contact dark room", "man serious expression shadow noir"])
    elif "history" in niche_l or "what if" in comb_l:
        if kw_tokens:
            queries.append(" ".join(kw_tokens))
        if ent_tokens:
            queries.append(f"{' '.join(ent_tokens)} historical cinematic")
        queries.append("dramatic fire smoke cinematic")
    else:
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
    return unique_q[:3] or ["cinematic dramatic motion"]


def _splice_dual_beat_hook_clip(
    clip_a_path: str,
    start_a: float,
    clip_b_path: str,
    start_b: float,
    total_duration_sec: float,
    output_path: str,
    work_dir: Path,
    is_scifi: bool = False,
    niche: str = "action",
    fair_use_a: bool = False,
    fair_use_b: bool = False,
) -> bool:
    """
    Dựng Hook 2 Nhịp Cắt Nhanh (Dual-Beat Fast-Cut Hook) ngay trong Cảnh 1:
    - Beat 1A (0.0s -> ~1.75s): Cú sốc thị giác / Hành động mở màn từ ứng viên #1.
    - Beat 1B (~1.75s -> hết Cảnh 1): Đổi góc máy sang ứng viên #2 (hoặc góc cận cảnh phản ứng/cao trào),
      tạo Pattern Interrupt ở giây thứ 1.75 để chặn phản xạ vuốt bỏ ở giây thứ 2.0!
    - Đảm bảo tổng thời lượng khớp chính xác 100% với `total_duration_sec` (0ms lệch audio).
    """
    if total_duration_sec < 2.6:
        return False
    dur_a = round(min(1.80, max(1.35, total_duration_sec * 0.48)), 2)
    dur_b = round(max(0.85, total_duration_sec - dur_a), 2)
    beat_a_out = str(work_dir / "hook_beat_1a_tmp.mp4")
    beat_b_out = str(work_dir / "hook_beat_1b_tmp.mp4")
    concat_list_path = work_dir / "hook_dual_beat_concat.txt"

    try:
        Path(beat_a_out).unlink(missing_ok=True)
        Path(beat_b_out).unlink(missing_ok=True)
        trim_video(
            input_path=clip_a_path,
            duration_seconds=dur_a,
            output_path=beat_a_out,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=niche,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=fair_use_a,
            start_offset_sec=start_a,
            preserve_continuous_flow=True,
        )
        trim_video(
            input_path=clip_b_path,
            duration_seconds=dur_b,
            output_path=beat_b_out,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=niche,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=fair_use_b,
            start_offset_sec=start_b,
            preserve_continuous_flow=True,
        )
        if not (Path(beat_a_out).exists() and Path(beat_b_out).exists()):
            return False
        if Path(beat_a_out).stat().st_size < 8_000 or Path(beat_b_out).stat().st_size < 8_000:
            return False

        p_a = str(Path(beat_a_out).resolve()).replace("\\", "/")
        p_b = str(Path(beat_b_out).resolve()).replace("\\", "/")
        concat_list_path.write_text(f"file '{p_a}'\nfile '{p_b}'\n", encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path),
            "-t", f"{total_duration_sec:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30", "-an",
            output_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        ok = Path(output_path).exists() and Path(output_path).stat().st_size > 15_000
        return ok
    except Exception as e_db:
        logger.debug(f"[HookVideoNode] Dual-beat splice skip: {e_db}")
        return False
    finally:
        for tmp_p in (beat_a_out, beat_b_out, str(concat_list_path)):
            try:
                Path(tmp_p).unlink(missing_ok=True)
            except Exception:
                pass


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

    def _get_scene_dur_sec(sid: int, default_s: float = 3.5) -> float:
        sc_a = next((s for s in scenes_audio if isinstance(s, dict) and int(s.get("scene_id") or 0) == sid), {})
        ms = sc_a.get("audio_duration_ms") or sc_a.get("duration_ms") or 0
        if ms:
            return max(float(ms) / 1000.0, 1.2)
        if 0 < sid <= len(base_scenes):
            b_sc = base_scenes[sid - 1]
            est = b_sc.get("estimated_seconds") if isinstance(b_sc, dict) else getattr(b_sc, "estimated_seconds", None)
            if est:
                return max(float(est), 1.2)
        return default_s

    scene_1 = dict(base_scenes[0]) if isinstance(base_scenes[0], dict) else {}
    audio_1 = next((s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id") == 1), {})

    topic = state.get("topic") or script_data.get("topic") or ""
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity") or ""
    niche = state.get("channel_style") or state.get("niche") or "general"
    visual_keyword = scene_1.get("visual_keyword") or topic
    hook_narration = scene_1.get("narration") or audio_1.get("narration") or script_data.get("hook") or ""

    dur_s1 = _get_scene_dur_sec(1, default_s=3.5)
    dur_s2 = _get_scene_dur_sec(2, default_s=3.2) if len(base_scenes) >= 2 else 0.0
    # VideoAgent dùng concat trực tiếp (transition_duration=0.0) cho các cảnh không phải cảnh cuối,
    # nên thời lượng clip Cảnh 1 & Cảnh 2 phải khớp chính xác 100% với dur_s1, dur_s2 (0ms lệch phụ đề/lời đọc)
    transition_duration = 0.0 if len(base_scenes) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
    target_hook_duration_sec = dur_s1 + transition_duration
    span_opening_two_scenes = bool(len(base_scenes) >= 2 and (dur_s1 < 3.8 or (dur_s1 + dur_s2) <= 7.2))
    opening_hook_window_sec = round((dur_s1 + dur_s2) if span_opening_two_scenes else target_hook_duration_sec, 2)

    keys = settings.get_gemini_keys_pool() if hasattr(settings, "get_gemini_keys_pool") else []
    if not keys:
        raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 1: CỔNG PHÂN TÍCH KỊCH BẢN & ĐẠO DIỄN HOOK BẰNG GEMINI (SCRIPT-AWARE HOOK DIRECTOR)
    # ──────────────────────────────────────────────────────────────────────────
    full_script_text = " | ".join(
        f"Scene {s.get('scene_id', i + 1)}: {s.get('narration', '')} ({s.get('visual_keyword', '')})"
        for i, s in enumerate(base_scenes[:9])
        if isinstance(s, dict)
    )
    fallback_queries = _build_hook_video_search_queries(
        topic=topic,
        primary_entity=primary_entity,
        visual_keyword=visual_keyword,
        niche=niche,
        full_script_text=full_script_text,
    )
    default_climax_scene_id = 4 if len(base_scenes) >= 5 else (3 if len(base_scenes) >= 3 else (2 if len(base_scenes) >= 2 else 1))

    pre_mode = str(script_data.get("hook_visual_mode") or "").strip().lower()
    pre_abstract = script_data.get("is_abstract_keyword")
    pre_climax_id = script_data.get("climax_scene_id")
    pre_queries = script_data.get("hook_search_queries")

    hook_visual_mode: str = "scene" if pre_mode == "scene" else "video"
    is_abstract_keyword: bool = bool(pre_abstract) if isinstance(pre_abstract, bool) else (hook_visual_mode == "scene")
    climax_scene_id: int = int(pre_climax_id) if isinstance(pre_climax_id, int) and 1 < pre_climax_id <= len(base_scenes) else default_climax_scene_id
    queries: List[str] = (
        [str(q).strip() for q in pre_queries if str(q).strip()][:3]
        if isinstance(pre_queries, list) and pre_queries
        else fallback_queries
    )
    climax_desc: str = f"{primary_entity or topic} — {visual_keyword}"
    visual_hook_blueprint: str = climax_desc
    hook_production_strategy: str = "VIRAL_SHORT_ACTION_PEAK"
    beat_1_query: str = queries[0] if queries else visual_keyword
    beat_2_query: str = queries[1] if len(queries) > 1 else beat_1_query
    beat_1_subject: str = primary_entity or topic
    beat_2_subject: str = primary_entity or topic
    stock_broll_queries: List[str] = []
    atmosphere_fx: str = "action"
    is_3d_cgi_subject: bool = False

    plan_prompt = f"""You are an elite Viral YouTube Shorts Hook Director & Script Visual Analyst.
Read the ENTIRE script below from Scene 1 to the final scene, understand the true core payoff of the story, and design the most scroll-stopping 0–3.5s Visual Hook for Scene 1.

- Topic: {topic}
- Primary Subject: {primary_entity or topic}
- Channel Niche: {niche}
- Opening Hook Narration (Scene 1): {hook_narration}
- Scene 1 Visual Keyword: {visual_keyword}
- Full Script (All Scenes): {full_script_text[:1600]}

CRITICAL DIRECTORIAL RULES:
1. DO NOT translate Scene 1's words blindly if Scene 1 is metaphorical, rhetorical, or a setup line (e.g., "One man refused the order to end the world" or "FBI analysts never look at your face"). Instead, read the FULL script to identify the concrete physical subject/climax and bring that visual payoff right into the first 3 seconds (Visual Foreshadowing).
2. Choose "hook_production_strategy" from EXACTLY one of these 3 modes based on what fits this specific topic & hook best:
   - "VIRAL_SHORT_ACTION_PEAK": Best for wildlife predators, animal rescues, underwater/ocean phenomena, natural disasters, or 3D CGI monsters where real action footage or 3D VFX from a viral Short creates an instant visceral shock.
   - "CINEMATIC_BROLL_FAST_CUT": Best for psychology, dark psychology, human behavior, FBI interrogation, body language, or interpersonal tension where YouTube Shorts searches would return talking heads/podcasts. We instead fetch 2 moody, clean HD B-Roll video clips of concrete physical human expressions/body language (e.g. nervous hands under table, cold fake smile, intense eye contact in shadows) and fast-cut them in Scene 1.
   - "HYBRID_MOTION_AND_SCENE_MONTAGE": Best for counterfactual history ("What If"), ancient empires, historical events, or conceptual mysteries where combining dramatic physical motion footage (Beat 1A) with a rapid multi-shot historical/cinematic scene montage (Beat 1B) fits the script hook best.
3. Translate any abstract concept into two distinct, concrete physical English queries ("beat_1_query" for 0.0–1.8s opening shock, and "beat_2_query" for 1.8–3.5s angle shift/reaction):
   - NEVER use abstract words ("manipulation", "psychology", "envy", "alternate history", "what if") in queries.
   - NEVER use "vs" or "versus" in a single query. Dedicate each query to ONE concrete subject + physical action/habitat.
   - Also provide "beat_1_subject" and "beat_2_subject" (bare 1-2 word noun of the subject in Beat 1 and Beat 2) and "stock_broll_queries" (2 concise 2-3 word noun + natural habitat/feature queries optimized for Pexels/Pixabay stock B-roll indexers, without complex verbs like "biting skull" or "thrashing").

Return ONLY valid JSON:
{{
  "canonical_subject": "bare 1-2 word noun without adjectives",
  "beat_1_subject": "bare 1-2 word subject noun for Beat 1",
  "beat_2_subject": "bare 1-2 word subject noun for Beat 2",
  "is_3d_cgi_subject": false,
  "hook_production_strategy": "VIRAL_SHORT_ACTION_PEAK | CINEMATIC_BROLL_FAST_CUT | HYBRID_MOTION_AND_SCENE_MONTAGE",
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "visual_hook_blueprint": "1-sentence description of the exact visual payoff that must appear in 0-3s to match this script's hook",
  "beat_1_query": "concrete physical subject + action/setting for 0.0-1.8s (2-4 words)",
  "beat_2_query": "distinct angle/reaction/subject for 1.8-3.5s fast cut (2-4 words)",
  "stock_broll_queries": ["subject1 habitat/close up (2-3 words)", "subject2 habitat/close up (2-3 words)"],
  "climax_scene_id": {climax_scene_id},
  "climax_description": "1-sentence description of mid-video climax",
  "search_queries": ["query 1", "query 2", "query 3"],
  "atmosphere_fx": "action | psychology | mystery | history"
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
                        beat_1_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(data.get("beat_1_subject") or canonical_subject or primary_entity or "")).strip()
                        beat_2_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(data.get("beat_2_subject") or canonical_subject or primary_entity or "")).strip()
                        is_3d_cgi_subject = bool(data.get("is_3d_cgi_subject", False))
                        strat_raw = str(data.get("hook_production_strategy") or "").strip().upper()
                        if strat_raw in ("VIRAL_SHORT_ACTION_PEAK", "CINEMATIC_BROLL_FAST_CUT", "HYBRID_MOTION_AND_SCENE_MONTAGE"):
                            hook_production_strategy = strat_raw
                        mode_raw = str(data.get("hook_visual_mode") or "").strip().lower()
                        abs_raw = bool(data.get("is_abstract_keyword", mode_raw == "scene"))
                        if mode_raw in ("scene", "image", "ai_image", "photo") or abs_raw:
                            hook_visual_mode = "scene"
                            is_abstract_keyword = True
                        else:
                            hook_visual_mode = "video"
                            is_abstract_keyword = False

                        b1_raw = re.sub(r"[^a-zA-Z0-9\s]", " ", str(data.get("beat_1_query") or "")).strip()
                        b2_raw = re.sub(r"[^a-zA-Z0-9\s]", " ", str(data.get("beat_2_query") or "")).strip()
                        raw_sb = [
                            re.sub(r"[^a-zA-Z0-9\s]", " ", str(sq)).strip()
                            for sq in (data.get("stock_broll_queries") or [])
                            if str(sq).strip()
                        ]
                        stock_broll_queries = [re.sub(r"\s+", " ", sq).strip() for sq in raw_sb if len(sq.split()) >= 1][:3]
                        raw_q = [
                            re.sub(r"[^a-zA-Z0-9\s]", " ", str(q)).strip()
                            for q in ([b1_raw, b2_raw] + list(data.get("search_queries") or []))
                            if str(q).strip()
                        ]
                        dedup_q: List[str] = []
                        for rq in raw_q:
                            rq_c = re.sub(r"\s+", " ", rq).strip()
                            if len(rq_c.split()) >= 2 and rq_c.lower() not in [x.lower() for x in dedup_q]:
                                dedup_q.append(rq_c)
                        if dedup_q:
                            queries = dedup_q[:3]
                            beat_1_query = queries[0]
                            beat_2_query = queries[1] if len(queries) > 1 else queries[0]

                        c_id_val = int(data.get("climax_scene_id") or climax_scene_id)
                        if 1 < c_id_val <= len(base_scenes):
                            climax_scene_id = c_id_val

                        climax_desc = str(data.get("climax_description") or climax_desc).strip()
                        visual_hook_blueprint = str(data.get("visual_hook_blueprint") or climax_desc).strip()
                        atmosphere_fx = str(data.get("atmosphere_fx") or niche or "action").lower()
                        gemini_decided = True
                        break
            elif resp.status_code in (429, 403, 404, 503):
                rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
        except Exception as e:
            rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e))
            logger.debug(f"[HookVideoNode] Gemini Hook Mode classifier fallback ({model_name}): {e}")

    if not gemini_decided:
        combined_text = f"{topic} {hook_narration} {visual_keyword} {niche}".lower()
        if any(k in combined_text for k in ("psych", "manipulat", "gaslight", "narcissist", "love bomb", "mindset", "body language", "liar", "interrogat")):
            hook_production_strategy = "CINEMATIC_BROLL_FAST_CUT"
            hook_visual_mode = "video"
            is_abstract_keyword = True
        elif any(k in combined_text for k in ("what if", "alternate history", "ancient", "empire", "world war", "soviet")):
            hook_production_strategy = "HYBRID_MOTION_AND_SCENE_MONTAGE"
            hook_visual_mode = "scene"
            is_abstract_keyword = True
        else:
            hook_production_strategy = "VIRAL_SHORT_ACTION_PEAK"
            hook_visual_mode = "video"
            is_abstract_keyword = False

    pre_resolved_hook_media: Dict[int, Dict[str, Any]] = {}

    logger.info(
        f"🎯 [HookVideoNode] Chiến lược Hook do AI chọn từ kịch bản: strategy='{hook_production_strategy}' "
        f"(mode='{hook_visual_mode}') | Blueprint: '{visual_hook_blueprint[:75]}' | "
        f"Beat1='{beat_1_query}' + Beat2='{beat_2_query}' | Queries={queries} | "
        f"HookWindow={opening_hook_window_sec:.2f}s (S1={dur_s1:.2f}s, S2={dur_s2:.2f}s) | ClimaxScene=#{climax_scene_id}"
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
    candidate_views: Dict[str, int] = {}
    validated_peaks: Dict[str, Dict[str, Any]] = {}
    is_scifi_pre = "scifi" in atmosphere_fx or "scifi" in niche.lower()
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

    _COPYRIGHT_CLAIM_BLOCKLIST = (
        "all rights reserved", "licensed to", "jukin media", "viralhog", "newsflare",
        "caters news", "bviral", "storyful", "collab", "lpe360", "rm videos",
    )

    cand_seq_idx = 0
    yt_queries_to_run = (
        queries[:1]
        if hook_production_strategy == "CINEMATIC_BROLL_FAST_CUT"
        else queries
    )
    subject_anchor_words = {
        w.lower()
        for src_s in (canonical_subject, beat_1_subject, beat_2_subject, primary_entity, topic)
        for w in re.findall(r"[a-zA-Z]{3,}", str(src_s or "").lower())
        if w.lower() not in {"the", "and", "for", "with", "from", "into", "that", "this", "why", "how", "what", "when", "fatal", "nature", "defies", "ambush", "secret", "truth"}
    }
    for q_idx, q in enumerate(yt_queries_to_run):
        if len(candidate_videos) >= 4:
            break
        # Quét danh sách ứng viên qua yt_dlp flat search (max_results=25), ưu tiên mạnh các Short VIRAL triệu view,
        # chặn kênh rủi ro bản quyền/watermark và xếp hạng bằng Mini-RAG
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
            with yt_dlp.YoutubeDL(license_policy.youtube_search_opts(flat_opts, max_results=25)) as ydl_flat:
                flat_res = ydl_flat.extract_info(license_policy.youtube_search_target(q, max_results=25), download=False)
            raw_flat_cands: List[Dict[str, Any]] = []
            for entry in ((flat_res or {}).get("entries") or []):
                if not isinstance(entry, dict):
                    continue
                vid_id = str(entry.get("id") or "").strip()
                if not vid_id:
                    continue
                dur_e = float(entry.get("duration") or 0)
                if dur_e <= 0 or dur_e < 4 or dur_e > 90:
                    continue
                title_e = str(entry.get("title") or "")
                uploader_e = str(entry.get("uploader") or entry.get("channel") or "")
                meta_e = f"{title_e} {uploader_e}".lower()
                if (
                    any(bad in meta_e for bad in _TALKING_HEAD_BLOCKLIST)
                    or any(bad_c in meta_e for bad_c in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST)
                    or any(cp_w in meta_e for cp_w in _COPYRIGHT_CLAIM_BLOCKLIST)
                ):
                    continue
                title_low = title_e.lower()
                anchor_hits = sum(1 for aw in subject_anchor_words if aw in title_low)
                if subject_anchor_words and anchor_hits == 0 and hook_production_strategy != "CINEMATIC_BROLL_FAST_CUT":
                    continue
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
                views_e = int(entry.get("view_count") or 0)
                viral_boost = (
                    65.0 if views_e >= 10_000_000 else
                    45.0 if views_e >= 1_000_000 else
                    25.0 if views_e >= 250_000 else
                    10.0 if views_e >= 50_000 else 0.0
                )
                short_fmt_bonus = 25.0 if (0 < dur_e <= 75) else 0.0
                raw_flat_cands.append({
                    "id": vid_id,
                    "url": vid_url,
                    "title": title_e,
                    "uploader": uploader_e,
                    "duration": dur_e,
                    "view_count": views_e,
                    "score": (anchor_hits * 45.0) + min(views_e / 15000.0, 75.0) + viral_boost + short_fmt_bonus,
                })

            ranked_flat = rag.rank_video_candidates_for_scene(
                job_id=job_id,
                candidates=raw_flat_cands,
                scene_narration=hook_narration,
                scene_query=q,
                topic=topic,
                subject_entity=canonical_subject or primary_entity or climax_desc,
                channel_id=channel_id,
            )
            for rc in ranked_flat[:3]:
                selected_urls.append(rc["url"])
                candidate_titles[rc["id"]] = rc.get("title", "")
                candidate_uploaders[rc["id"]] = rc.get("uploader", "")
                candidate_views[rc["id"]] = int(rc.get("view_count") or 0)
        except Exception as e_flat:
            logger.debug(f"[HookVideoNode] yt_dlp flat pre-filter skip ({q}): {e_flat}")

        if not selected_urls:
            continue

        out_tmpl = str(hook_cand_dir / f"yt_hook_cand_{q_idx}_%(id)s.%(ext)s")
        dl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 10,
            "retries": 1,
            "nopart": True,
            "max_filesize": 28 * 1024 * 1024,
            "format": "bestvideo[protocol=https][height>=720][height<=1920][ext=mp4]/bestvideo[protocol=https][height>=480][height<=1920][ext=mp4]/bestvideo[height>=720][height<=1920][ext=mp4]/bestvideo[height>=480][height<=1920]/bestvideo/best",
            "merge_output_format": "mp4",
            "outtmpl": out_tmpl,
            "ignoreerrors": True,
        }
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                ydl_dl.download(selected_urls[:3])
        except Exception as e_yt:
            logger.debug(f"[HookVideoNode] yt-dlp download skip ({q}): {e_yt}")

        for mp4 in sorted(hook_cand_dir.glob(f"yt_hook_cand_{q_idx}_*.*")):
            if mp4.suffix.lower() not in (".mp4", ".webm", ".mkv", ".mov"):
                continue
            v_str = str(mp4)
            if mp4.stat().st_size <= 60_000 or v_str in candidate_videos or v_str in validated_peaks:
                continue

            peak_offset_sec, peak_duration_sec = find_hook_peak_window(
                v_str,
                min_duration=max(target_hook_duration_sec, 3.0),
                max_duration=15.0,
            )
            pre_clean_dur = min(max(peak_duration_sec, target_hook_duration_sec, 3.5), 8.5)
            clean_peak_path = str(hook_cand_dir / f"hook_peak_clean_{cand_seq_idx}.mp4")
            cand_seq_idx += 1
            try:
                trim_video(
                    input_path=v_str,
                    duration_seconds=pre_clean_dur,
                    output_path=clean_peak_path,
                    width=1080,
                    height=1920,
                    fps=30,
                    is_scifi=is_scifi_pre,
                    niche=atmosphere_fx,
                    apply_grading=False,
                    is_hook_scene=True,
                    fair_use_transform=True,
                    start_offset_sec=peak_offset_sec,
                    preserve_continuous_flow=True,
                )
            except Exception as e_tr:
                logger.debug(f"[HookVideoNode] Pre-trim peak skip ({v_str}): {e_tr}")

            inspect_target = clean_peak_path if (Path(clean_peak_path).exists() and Path(clean_peak_path).stat().st_size > 20_000) else v_str
            gate_res = VisualCritic.inspect_clip_before_concat(
                inspect_target,
                min_short_edge=720,
                min_sharpness=5.4,
                check_watermark=True,
                allow_snow=True,
                auto_sanitize=True,
            )
            if not gate_res.get("passed", False):
                # Thử cửa sổ Peak thứ 2 (cắt né vùng chữ/intro tại peak_offset_sec) trước khi loại bỏ video Short!
                alt_excl = [(max(0.0, peak_offset_sec - 1.5), peak_offset_sec + 5.0)]
                alt_offset, alt_dur = find_hook_peak_window(
                    v_str,
                    min_duration=max(target_hook_duration_sec, 3.0),
                    max_duration=15.0,
                    exclude_ranges=alt_excl,
                )
                if abs(alt_offset - peak_offset_sec) >= 2.0:
                    try:
                        Path(clean_peak_path).unlink(missing_ok=True)
                        trim_video(
                            input_path=v_str,
                            duration_seconds=min(max(alt_dur, target_hook_duration_sec, 3.5), 8.5),
                            output_path=clean_peak_path,
                            width=1080,
                            height=1920,
                            fps=30,
                            is_scifi=is_scifi_pre,
                            niche=atmosphere_fx,
                            apply_grading=False,
                            is_hook_scene=True,
                            fair_use_transform=True,
                            start_offset_sec=alt_offset,
                            preserve_continuous_flow=True,
                        )
                        inspect_target = clean_peak_path if (Path(clean_peak_path).exists() and Path(clean_peak_path).stat().st_size > 20_000) else v_str
                        gate_res = VisualCritic.inspect_clip_before_concat(
                            inspect_target,
                            min_short_edge=720,
                            min_sharpness=5.4,
                            check_watermark=True,
                            allow_snow=True,
                            auto_sanitize=True,
                        )
                        if gate_res.get("passed", False):
                            peak_offset_sec, peak_duration_sec = alt_offset, alt_dur
                    except Exception:
                        pass
            if not gate_res.get("passed", False):
                logger.info(
                    f"🚫 [HookVideoNode Gate] Loại bỏ ứng viên Hook '{mp4.name}' tại đoạn Peak @{peak_offset_sec:.1f}s vì: "
                    f"{'; '.join(gate_res.get('issues') or [])}"
                )
                Path(clean_peak_path).unlink(missing_ok=True)
                validated_peaks[v_str] = {"passed": False}
                continue

            validated_peaks[v_str] = {
                "passed": True,
                "peak_offset_sec": peak_offset_sec,
                "peak_duration_sec": peak_duration_sec,
                "clean_peak_path": clean_peak_path,
                "inspect_target": inspect_target,
            }
            candidate_videos.append(v_str)
            if len(candidate_videos) >= 4:
                break

    # ── BỔ SUNG ĐA NGUỒN VIDEO (PEXELS HD + PIXABAY HD + WIKIMEDIA COMMONS + NASA VIDEO) CHO DUAL-BEAT HOOK ──
    # Nạp tối đa 2 ứng viên Video HD sạch cho Beat 1 (0-1.8s) và Beat 2 (1.8-3.5s) để:
    # (1) Tạo nhịp cắt cảnh kép (Dual-Beat Fast-Cut) ngay trong Cảnh 1 chống vuốt bỏ ở giây 1.8s,
    # (2) Cung cấp B-Roll điện ảnh không chữ/watermark cho ngách Tâm lý (CINEMATIC_BROLL_FAST_CUT).
    if len(candidate_videos) < 6 and not is_3d_cgi_subject:
        try:
            from app.agents.media_agent import MediaAgent
            m_agent = MediaAgent()
            multi_beat_queries: List[str] = []
            for bq in [*stock_broll_queries, beat_1_subject, beat_2_subject, beat_1_query, beat_2_query]:
                bq_clean = str(bq or "").strip()
                if bq_clean and bq_clean.lower() not in {x.lower() for x in multi_beat_queries}:
                    multi_beat_queries.append(bq_clean)
            if not multi_beat_queries:
                multi_beat_queries = [canonical_subject or visual_keyword or topic]

            combined_hook_subjects = (
                f"{beat_1_subject} / {beat_2_subject}"
                if beat_1_subject and beat_2_subject and beat_1_subject.lower() != beat_2_subject.lower()
                else (canonical_subject or primary_entity or topic)
            )
            seen_multi_urls: set = set()
            added_stock_beats = 0
            for mb_idx, st_q_hook in enumerate(multi_beat_queries[:3]):
                if added_stock_beats >= 2 or len(candidate_videos) >= 6:
                    break
                va_multi_src = {
                    "analysis_source": "ai",
                    "subject": combined_hook_subjects,
                    "core_visual_entity": combined_hook_subjects,
                    "topic": topic,
                    "search_queries": [st_q_hook] + [q for q in multi_beat_queries[:3] if q != st_q_hook],
                    "is_hook_scene": True,
                }
                best_multi_v = m_agent.search_best_video_clip(
                    query=st_q_hook,
                    visual_analysis=va_multi_src,
                    narration=hook_narration,
                    topic=topic,
                )
                v_url_ms = (best_multi_v or {}).get("url")
                if v_url_ms and v_url_ms not in seen_multi_urls:
                    seen_multi_urls.add(v_url_ms)
                    multi_v_path = str(hook_cand_dir / f"multisrc_hook_cand_{cand_seq_idx}.mp4")
                    m_agent.download_file(v_url_ms, multi_v_path)
                    if Path(multi_v_path).exists() and Path(multi_v_path).stat().st_size > 40_000:
                        p_off, p_dur = find_hook_peak_window(
                            multi_v_path,
                            min_duration=max(target_hook_duration_sec, 3.0),
                            max_duration=15.0,
                        )
                        clean_ms_path = str(hook_cand_dir / f"hook_peak_clean_{cand_seq_idx}.mp4")
                        cand_seq_idx += 1
                        trim_video(
                            input_path=multi_v_path,
                            duration_seconds=min(max(p_dur, opening_hook_window_sec, 3.5), 8.5),
                            output_path=clean_ms_path,
                            width=1080,
                            height=1920,
                            fps=30,
                            is_scifi=is_scifi_pre,
                            niche=atmosphere_fx,
                            apply_grading=False,
                            is_hook_scene=True,
                            fair_use_transform=False,
                            start_offset_sec=p_off,
                            preserve_continuous_flow=True,
                        )
                        insp_ms = clean_ms_path if (Path(clean_ms_path).exists() and Path(clean_ms_path).stat().st_size > 20_000) else multi_v_path
                        ms_gate = VisualCritic.inspect_clip_before_concat(
                            insp_ms, min_short_edge=720, min_sharpness=5.2, check_watermark=True, allow_snow=True, auto_sanitize=True
                        )
                        if ms_gate.get("passed", False):
                            validated_peaks[multi_v_path] = {
                                "passed": True,
                                "peak_offset_sec": p_off,
                                "peak_duration_sec": p_dur,
                                "clean_peak_path": clean_ms_path,
                                "inspect_target": insp_ms,
                                "pre_curated_stock": True,
                                "vision_score": float(best_multi_v.get("vision_score") or 8.2),
                            }
                            candidate_videos.append(multi_v_path)
                            added_stock_beats += 1
                            candidate_titles[Path(multi_v_path).stem] = str(best_multi_v.get("title") or f"Multi-Source Beat #{mb_idx + 1}")
                            logger.info(
                                f"🌐 [HookVideoNode Multi-Source Beat #{mb_idx + 1}] Đã bổ sung video sạch từ '{best_multi_v.get('source', 'stock')}' "
                                f"cho query '{st_q_hook}': '{best_multi_v.get('title', '')[:50]}'"
                            )
        except Exception as e_ms:
            logger.debug(f"[HookVideoNode] Multi-source video expansion skip: {e_ms}")

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 3: QUÉT ĐOẠN PEAK TỰ NHIÊN (`peak_offset_sec`, `peak_duration_sec`), LỌC SẠCH WATERMARK 3 BƯỚC & CHẤM ĐIỂM VISION
    # (Gỡ khóa cứng 3s: Đo luôn cả độ dài thực sự của đoạn Peak tới 15s nếu cao trào kéo dài!)
    # ──────────────────────────────────────────────────────────────────────────
    scored_candidates: List[Dict[str, Any]] = []
    passing_candidates: List[Dict[str, Any]] = []
    best_candidate: Optional[Dict[str, Any]] = None

    for idx, v_path in enumerate(candidate_videos):
        pre_v = validated_peaks.get(v_path)
        if pre_v and pre_v.get("passed"):
            peak_offset_sec = float(pre_v["peak_offset_sec"])
            peak_duration_sec = float(pre_v["peak_duration_sec"])
            clean_peak_path = str(pre_v["clean_peak_path"])
            inspect_target = str(pre_v["inspect_target"])
            if pre_v.get("pre_curated_stock"):
                v_sc = float(pre_v.get("vision_score") or 8.2)
                score_res = {
                    "video_path": v_path,
                    "clean_peak_path": inspect_target,
                    "peak_offset_sec": peak_offset_sec,
                    "peak_duration_sec": peak_duration_sec,
                    "recommended_hook_mode": "video",
                    "entity_match_score": v_sc,
                    "motion_action_score": 8.2,
                    "scroll_stop_score": 8.0,
                    "clean_frame_score": 9.5,
                    "overall_score": round(v_sc * 0.40 + 8.2 * 0.25 + 8.0 * 0.20 + 9.5 * 0.15, 2),
                    "passed": True,
                    "reason": f"Pre-curated HD Stock Video Beat (VisionScore={v_sc}/10)",
                }
                scored_candidates.append(score_res)
                passing_candidates.append(score_res)
                logger.info(
                    f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name} @Peak {peak_offset_sec:.1f}s, len={peak_duration_sec:.1f}s): "
                    f"Mode=video | Overall={score_res['overall_score']}/10 -> ✅ ĐẠT (Video B-Roll HD đã qua Vision Curation)"
                )
                continue
        else:
            # 3.1. Quét toàn bộ video Short để tìm đúng mốc Peak & đo trọn vẹn độ dài đoạn Peak tự nhiên (tới 15s)
            peak_offset_sec, peak_duration_sec = find_hook_peak_window(
                v_path,
                min_duration=target_hook_duration_sec,
                max_duration=15.0,
            )

            # 3.1b. Cắt riêng đoạn Peak (kèm fair_use_transform để gọt lề chữ trên/dưới) rồi chạy cổng 3 bước (Cắt né -> Delogo/Crop -> Bỏ)
            clean_peak_path = str(hook_cand_dir / f"hook_peak_clean_ext_{idx}.mp4")
            try:
                trim_video(
                    input_path=v_path,
                    duration_seconds=max(target_hook_duration_sec, 3.0),
                    output_path=clean_peak_path,
                    width=1080,
                    height=1920,
                    fps=30,
                    is_scifi=is_scifi_pre,
                    niche=atmosphere_fx,
                    apply_grading=False,
                    is_hook_scene=True,
                    fair_use_transform=True,
                    start_offset_sec=peak_offset_sec,
                    preserve_continuous_flow=True,
                )
            except Exception as e_tr:
                logger.debug(f"[HookVideoNode] Pre-trim peak skip ({v_path}): {e_tr}")

            inspect_target = clean_peak_path if (Path(clean_peak_path).exists() and Path(clean_peak_path).stat().st_size > 20_000) else v_path
            gate_res = VisualCritic.inspect_clip_before_concat(
                inspect_target,
                min_short_edge=720,
                min_sharpness=5.4,
                check_watermark=True,
                allow_snow=True,
                auto_sanitize=True,
            )
            if not gate_res.get("passed", False):
                logger.info(
                    f"🚫 [HookVideoNode Gate] Loại bỏ ứng viên Hook '{Path(v_path).name}' tại đoạn Peak @{peak_offset_sec:.1f}s vì: "
                    f"{'; '.join(gate_res.get('issues') or [])}"
                )
                Path(clean_peak_path).unlink(missing_ok=True)
                continue

        # 3.2. Trích xuất 3 khung hình đại diện NGAY TẠI ĐOẠN PEAK ĐÃ XỬ LÝ SẠCH (`inspect_target`)
        sheet_path = hook_cand_dir / f"hook_score_sheet_{idx}.jpg"
        win_dur = max(target_hook_duration_sec, 2.4)
        fps_expr = f"3/{win_dur:.2f}"
        seek_args = [] if inspect_target == clean_peak_path else (["-ss", f"{peak_offset_sec:.2f}"] if peak_offset_sec >= 0.15 else [])
        cmd_sheet = [
            "ffmpeg", "-y",
            *seek_args,
            "-t", f"{win_dur:.2f}",
            "-i", inspect_target,
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
        vision_prompt = f"""You are a strict YouTube Shorts Hook Video Quality Grader & Scroll-Stop Director.
Evaluate this 3-frame sequence extracted directly from the PEAK CLIMAX window (starting at {peak_offset_sec:.1f}s, natural peak length {peak_duration_sec:.1f}s) of a candidate Short video for:
- Video Topic: "{topic}"
- Valid Subjects/Entities for Hook: "{beat_1_subject}, {beat_2_subject}, or {primary_entity}"
- Opening Hook Line: "{hook_narration}"
- Hook Production Strategy: "{hook_production_strategy}"

CRITICAL GRADING CALIBRATION (REAL VIDEO HOOK PRIORITY):
- Real moving video footage of ANY of the core subjects ("{beat_1_subject}", "{beat_2_subject}", or "{primary_entity}") in their authentic natural environment/habitat (stalking, swimming, prowling, hunting, close-up detail, or dramatic motion) is ALWAYS 10x better for a YouTube Shorts Hook than downgrading to static photos ("scene")!
- DO NOT reject a real wild video just because it shows stalking/prowling/swimming rather than a literal movie-like explosion/strike, as we combine multiple video beats in a fast-cut!
- ONLY return "recommended_hook_mode": "scene" (rejecting the video) if the footage shows: (a) the WRONG species/subject not in "{topic}", (b) an animal trapped in an artificial zoo enclosure/concrete wall/cage when wild nature is required, (c) a contradicting biome/season (e.g. snow when tropical jungle is required), (d) a talking head/podcast/vlog, or (e) burned-in subtitles, giant text overlays, or watermarks.

Score strictly from 1.0 to 10.0:
1. "recommended_hook_mode": "video" if it shows any valid subject from "{topic}" in an authentic setting with clean frames; "scene" ONLY if it violates (a)-(e) above.
2. "entity_match_score": Does the clip show a valid subject from "{topic}" in an authentic natural habitat/setting? (7.5-10 = authentic subject in wild/natural habitat; <= 4.0 = wrong species, zoo cage/artificial wall, or contradicting biome).
3. "motion_action_score": Is it real footage with physical movement/life rather than a talking head or static slide? (7.5-10 = real wildlife/cinematic footage; 1-3 = talking head or static slide).
4. "scroll_stop_score": Visual quality & intrigue for a fast-cut Hook (7.0-10 = clear, engaging footage of the subject; <= 5.0 = blurry, obstructed, or boring empty background).
5. "clean_frame_score": Is the frame clean without burned-in subtitles, text overlays, split-screen collages, or watermarks? (8.0-10 = clean footage; <= 4.0 = visible watermark/logo or subtitles).
6. "reason": Short 1-sentence explanation.

Return ONLY valid JSON:
{{"recommended_hook_mode": "video", "entity_match_score": 8.5, "motion_action_score": 8.5, "scroll_stop_score": 8.2, "clean_frame_score": 8.5, "reason": "..."}}"""

        score_res: Optional[Dict[str, Any]] = None
        for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=6):
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
                resp = requests.post(url, json=payload, timeout=12)
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
                            s_score = float(data.get("scroll_stop_score", m_score))
                            c_score = float(data.get("clean_frame_score", 5.0))
                            overall = round(e_score * 0.40 + m_score * 0.25 + s_score * 0.20 + c_score * 0.15, 2)
                            min_scroll_req = 6.5 if hook_production_strategy == "HYBRID_MOTION_AND_SCENE_MONTAGE" else 5.8
                            passed = (
                                (rec_mode == "video" or e_score >= 6.5)
                                and (overall >= 6.2)
                                and (e_score >= 5.8)
                                and (c_score >= 5.8)
                                and (m_score >= 5.5)
                                and (s_score >= min_scroll_req)
                            )
                            score_res = {
                                "video_path": v_path,
                                "clean_peak_path": inspect_target,
                                "peak_offset_sec": peak_offset_sec,
                                "peak_duration_sec": peak_duration_sec,
                                "recommended_hook_mode": "video" if passed else "scene",
                                "entity_match_score": e_score,
                                "motion_action_score": m_score,
                                "scroll_stop_score": s_score,
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

        # Nếu cửa sổ Peak #1 của video Short chưa đạt (do đoạn đầu bị khuất/chưa xuất hiện chủ thể hoặc chưa tới đoạn cao trào):
        # Tự động quét cửa sổ Peak #2 (loại trừ vùng Peak #1) ngay trong cùng video Short đó và kiểm định lại bằng Gemini Vision!
        if score_res is not None and not score_res.get("passed"):
            try:
                alt_excl_v = [(max(0.0, peak_offset_sec - 1.5), peak_offset_sec + 5.5)]
                alt_off2, alt_dur2 = find_hook_peak_window(
                    v_path,
                    min_duration=max(target_hook_duration_sec, 3.0),
                    max_duration=15.0,
                    exclude_ranges=alt_excl_v,
                )
                if abs(alt_off2 - peak_offset_sec) >= 2.0:
                    alt_clean_path = str(hook_cand_dir / f"hook_peak_clean_alt2_{idx}.mp4")
                    trim_video(
                        input_path=v_path,
                        duration_seconds=min(max(alt_dur2, opening_hook_window_sec, 3.5), 8.5),
                        output_path=alt_clean_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi_pre,
                        niche=atmosphere_fx,
                        apply_grading=False,
                        is_hook_scene=True,
                        fair_use_transform=True,
                        start_offset_sec=alt_off2,
                        preserve_continuous_flow=True,
                    )
                    if Path(alt_clean_path).exists() and Path(alt_clean_path).stat().st_size > 20_000:
                        g_alt2 = VisualCritic.inspect_clip_before_concat(
                            alt_clean_path, min_short_edge=720, min_sharpness=5.4, check_watermark=True, allow_snow=True, auto_sanitize=True
                        )
                        if g_alt2.get("passed", False):
                            alt_sheet = hook_cand_dir / f"hook_score_sheet_alt2_{idx}.jpg"
                            subprocess.run(
                                [
                                    "ffmpeg", "-y", "-t", f"{win_dur:.2f}", "-i", alt_clean_path,
                                    "-vf", f"fps={fps_expr},scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
                                    "-frames:v", "1", "-q:v", "4", str(alt_sheet),
                                ],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False,
                            )
                            if alt_sheet.exists() and alt_sheet.stat().st_size > 5_000:
                                b64_alt2 = base64.b64encode(alt_sheet.read_bytes()).decode("ascii")
                                for m_n2, k_2 in rotator.iter_best_model_key_pairs(max_total_attempts=4):
                                    try:
                                        u2 = f"https://generativelanguage.googleapis.com/v1beta/models/{m_n2}:generateContent?key={k_2}"
                                        r2 = requests.post(
                                            u2,
                                            json={"contents": [{"parts": [{"text": vision_prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_alt2}}]}]},
                                            timeout=12,
                                        )
                                        if r2.status_code == 200:
                                            c2 = r2.json().get("candidates", [])
                                            if c2:
                                                t2 = "".join(p.get("text", "") for p in c2[0].get("content", {}).get("parts", []))
                                                m2 = re.search(r"\{.*\}", t2, re.DOTALL)
                                                if m2:
                                                    d2 = json.loads(m2.group(0))
                                                    rec2 = str(d2.get("recommended_hook_mode") or "video").strip().lower()
                                                    e2 = float(d2.get("entity_match_score", 5.0))
                                                    m2_s = float(d2.get("motion_action_score", 5.0))
                                                    s2_s = float(d2.get("scroll_stop_score", m2_s))
                                                    c2_s = float(d2.get("clean_frame_score", 5.0))
                                                    ov2 = round(e2 * 0.40 + m2_s * 0.25 + s2_s * 0.20 + c2_s * 0.15, 2)
                                                    pass2 = (
                                                        (rec2 == "video" or e2 >= 6.5)
                                                        and (ov2 >= 6.2)
                                                        and (e2 >= 5.8)
                                                        and (c2_s >= 5.8)
                                                        and (m2_s >= 5.5)
                                                        and (s2_s >= min_scroll_req)
                                                    )
                                                    if pass2 or ov2 > float(score_res.get("overall_score", 0.0)):
                                                        peak_offset_sec, peak_duration_sec = alt_off2, alt_dur2
                                                        inspect_target = alt_clean_path
                                                        score_res = {
                                                            "video_path": v_path,
                                                            "clean_peak_path": alt_clean_path,
                                                            "peak_offset_sec": alt_off2,
                                                            "peak_duration_sec": alt_dur2,
                                                            "recommended_hook_mode": "video" if pass2 else "scene",
                                                            "entity_match_score": e2,
                                                            "motion_action_score": m2_s,
                                                            "scroll_stop_score": s2_s,
                                                            "clean_frame_score": c2_s,
                                                            "overall_score": ov2,
                                                            "passed": pass2,
                                                            "reason": f"[Window #2 @{alt_off2:.1f}s] {d2.get('reason', '')}",
                                                        }
                                                    break
                                        elif r2.status_code in (429, 403, 404, 503):
                                            rotator.mark_model_rate_limited(k_2, m_n2, status_code=r2.status_code, error_text=r2.text[:300])
                                    except Exception as e_v2:
                                        rotator.mark_model_rate_limited(k_2, m_n2, status_code=503, error_text=str(e_v2))
                                        continue
            except Exception as e_alt2:
                logger.debug(f"[HookVideoNode] Window #2 retry skip: {e_alt2}")

        if score_res is None:
            logger.warning(f"⚠️ [HookVideoGrader] Không thể xác thực Gemini Vision cho '{Path(v_path).name}' -> Bỏ qua để tránh lọt video chưa kiểm duyệt!")
            continue

        scored_candidates.append(score_res)
        logger.info(
            f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name} @Peak {peak_offset_sec:.1f}s, len={peak_duration_sec:.1f}s): "
            f"Mode={score_res['recommended_hook_mode']} | Overall={score_res['overall_score']}/10 "
            f"(Entity={score_res['entity_match_score']}, Motion={score_res['motion_action_score']}, "
            f"ScrollStop={score_res.get('scroll_stop_score', 0.0)}, Clean={score_res['clean_frame_score']}) "
            f"-> {'✅ ĐẠT (Dùng Video Peak)' if score_res['passed'] else '❌ LOẠI (Chuyển sang Tạo Cảnh)'} ({score_res['reason']})"
        )
        if score_res["passed"]:
            m_yt_cand = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(v_path).name)
            c_vid_id = m_yt_cand.group(1) if m_yt_cand else Path(v_path).stem
            score_res["view_count"] = int(candidate_views.get(c_vid_id, 0))
            passing_candidates.append(score_res)

    passing_candidates.sort(
        key=lambda x: (
            float(x.get("overall_score", 0.0)),
            float(x.get("scroll_stop_score", 0.0)),
            int(x.get("view_count", 0)),
        ),
        reverse=True,
    )
    if passing_candidates:
        best_candidate = passing_candidates[0]

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 4: CẮT ĐOẠN PEAK GHÉP VÀO ĐẦU VIDEO (ƯU TIÊN DUAL-BEAT FAST-CUT TRONG CẢNH 1 + MỞ RỘNG CẢNH 2 LIỀN MẠCH)
    # ──────────────────────────────────────────────────────────────────────────
    hook_output_clip = str(work_dir / "hook_motion_001.mp4")
    hook_output_clip_s2 = str(work_dir / "hook_motion_002.mp4")
    is_scifi = "scifi" in atmosphere_fx or "scifi" in niche.lower()

    if best_candidate and Path(best_candidate["video_path"]).exists():
        best_v_path = best_candidate["video_path"]
        clean_p_path = best_candidate.get("clean_peak_path") or best_v_path
        use_precleaned = clean_p_path != best_v_path and Path(clean_p_path).exists()
        peak_start = float(best_candidate.get("peak_offset_sec", 0.0))
        natural_peak_dur = float(best_candidate.get("peak_duration_sec") or target_hook_duration_sec)
        m_yt_hook = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(best_v_path).name)
        hook_vid_id = m_yt_hook.group(1) if m_yt_hook else ""
        hook_vid_title = candidate_titles.get(hook_vid_id) or candidate_titles.get(Path(best_v_path).stem, "")

        other_passing = [c for c in passing_candidates if c["video_path"] != best_v_path]
        used_second_paths = set()

        # 4.1. Thử tạo DUAL-BEAT FAST-CUT ngay trong Cảnh 1 (0.0-1.75s Beat 1A + 1.75s-end Beat 1B)
        # nếu có >= 2 ứng viên đạt chuẩn và Cảnh 1 >= 2.5s (chống phản xạ vuốt bỏ ở giây thứ 2.0!)
        dual_beat_spliced = False
        if other_passing and target_hook_duration_sec >= 2.6:
            beat_b_cand = other_passing[0]
            beat_b_v_path = beat_b_cand["video_path"]
            beat_b_clean = beat_b_cand.get("clean_peak_path") or beat_b_v_path
            use_b_precleaned = beat_b_clean != beat_b_v_path and Path(beat_b_clean).exists()
            peak_b_start = float(beat_b_cand.get("peak_offset_sec", 0.0))
            if Path(beat_b_clean).exists():
                dual_beat_spliced = _splice_dual_beat_hook_clip(
                    clip_a_path=clean_p_path if use_precleaned else best_v_path,
                    start_a=0.0 if use_precleaned else peak_start,
                    clip_b_path=beat_b_clean if use_b_precleaned else beat_b_v_path,
                    start_b=0.0 if use_b_precleaned else peak_b_start,
                    total_duration_sec=target_hook_duration_sec,
                    output_path=hook_output_clip,
                    work_dir=work_dir,
                    is_scifi=is_scifi,
                    niche=atmosphere_fx,
                    fair_use_a=not use_precleaned,
                    fair_use_b=not use_b_precleaned,
                )
                if dual_beat_spliced:
                    used_second_paths.add(beat_b_v_path)
                    logger.info(
                        f"⚡ [HookVideoNode Dual-Beat Fast-Cut] Đã ghép 2 góc máy liên tiếp cho Cảnh 1 "
                        f"({target_hook_duration_sec:.2f}s | Beat 1A='{Path(best_v_path).name}' + Beat 1B='{Path(beat_b_v_path).name}') "
                        f"-> {hook_output_clip}"
                    )

        if not dual_beat_spliced:
            logger.info(
                f"🔥 [HookVideoNode] Đã cắt đoạn PEAK NHẤT (@{peak_start:.2f}s, độ dài tự nhiên={natural_peak_dur:.2f}s, "
                f"Views={best_candidate.get('view_count', 0):,}, Score={best_candidate['overall_score']}/10, "
                f"ScrollStop={best_candidate.get('scroll_stop_score', 0.0)}/10) "
                f"ghép vào ĐẦU VIDEO (Cảnh 1: {target_hook_duration_sec:.2f}s) -> {hook_output_clip}"
            )
            trim_video(
                input_path=clean_p_path if use_precleaned else best_v_path,
                duration_seconds=target_hook_duration_sec,
                output_path=hook_output_clip,
                width=1080,
                height=1920,
                fps=30,
                is_scifi=is_scifi,
                niche=atmosphere_fx,
                apply_grading=True,
                is_hook_scene=True,
                fair_use_transform=not use_precleaned,
                start_offset_sec=0.0 if use_precleaned else peak_start,
                preserve_continuous_flow=True,
            )

        hook_mode = (
            f"DUAL_BEAT_FAST_CUT_{hook_production_strategy}"
            if dual_beat_spliced
            else "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK"
        )
        final_hook_visual_mode = "video"
        if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
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
                "visual_strategy": "HOOK_DUAL_BEAT_FAST_CUT" if dual_beat_spliced else "HOOK_PEAK_SHORT_VIDEO",
                "strategy_rationale": (
                    f"Dual-Beat Fast-Cut ({hook_production_strategy}, Score={best_candidate['overall_score']}/10, ScrollStop={best_candidate.get('scroll_stop_score', 0.0)}/10)"
                    if dual_beat_spliced
                    else f"Viral Peak Short @{peak_start:.1f}s (full_peak={natural_peak_dur:.1f}s, {best_candidate['overall_score']}/10)"
                ),
            }
            if scenes_media:
                scene_1["media_path"] = hook_output_clip
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = hook_mode
                scenes_media[0] = scene_1

        # ── MỞ RỘNG HOOK VIDEO ĐẦU VIDEO TRỌN VẸN 3-7 GIÂY (CẢNH 1 + CẢNH 2 LIỀN MẠCH, KHỚP 100% LỜI ĐỌC) ──
        if span_opening_two_scenes and dur_s2 >= 1.2 and len(base_scenes) >= 2:
            s2_filled = False
            # Ưu tiên 1: Nếu có video ứng viên Viral độc lập (chưa dùng ở Beat 1B và đủ độ dài Peak) -> Ghép nối tiếp sang Cảnh 2
            s2_ordered_cands = (
                [c for c in other_passing if c["video_path"] not in used_second_paths]
                + [c for c in other_passing if c["video_path"] in used_second_paths]
            )
            for cand_s2 in s2_ordered_cands:
                if float(cand_s2.get("peak_duration_sec") or 0.0) < max(dur_s2 - 0.8, 3.0):
                    continue
                s2_v_path = cand_s2["video_path"]
                s2_clean_p = cand_s2.get("clean_peak_path") or s2_v_path
                s2_use_pre = s2_clean_p != s2_v_path and Path(s2_clean_p).exists()
                m_yt_s2 = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(s2_v_path).name)
                s2_vid_id = m_yt_s2.group(1) if m_yt_s2 else ""
                s2_title = candidate_titles.get(s2_vid_id) or candidate_titles.get(Path(s2_v_path).stem, "")
                if not rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=s2_vid_id,
                    url=s2_v_path,
                    title=s2_title,
                    scene_id=2,
                ):
                    continue
                s2_peak_start = float(cand_s2.get("peak_offset_sec", 0.0))
                try:
                    trim_video(
                        input_path=s2_clean_p if s2_use_pre else s2_v_path,
                        duration_seconds=dur_s2,
                        output_path=hook_output_clip_s2,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=atmosphere_fx,
                        apply_grading=True,
                        is_hook_scene=True,
                        fair_use_transform=not s2_use_pre,
                        start_offset_sec=0.0 if s2_use_pre else s2_peak_start,
                        preserve_continuous_flow=True,
                    )
                    if Path(hook_output_clip_s2).exists() and Path(hook_output_clip_s2).stat().st_size > 10_000:
                        ok_s2, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=2,
                            media_path=hook_output_clip_s2,
                            vid_id=s2_vid_id,
                            url=s2_v_path,
                            title=s2_title,
                            is_continuation_slice=False,
                        )
                        if ok_s2:
                            used_second_paths.add(s2_v_path)
                            s2_filled = True
                            if s2_vid_id and license_policy.cc_only_enabled():
                                license_policy.record_credit(
                                    job_id=job_id,
                                    video_id=s2_vid_id,
                                    title=s2_title,
                                    uploader=candidate_uploaders.get(s2_vid_id, ""),
                                )
                            pre_resolved_hook_media[2] = {
                                "media_path": hook_output_clip_s2,
                                "media_type": "hook_ready_video",
                                "production_method": "HOOK_EXTENDED_2ND_VIRAL_VIDEO",
                                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                                "strategy_rationale": f"Extended 3-7s Hook (Scene 2 distinct viral clip @{s2_peak_start:.1f}s, {dur_s1 + dur_s2:.1f}s total)",
                            }
                            if len(scenes_media) >= 2 and isinstance(scenes_media[1], dict):
                                scenes_media[1]["media_path"] = hook_output_clip_s2
                                scenes_media[1]["media_type"] = "hook_ready_video"
                                scenes_media[1]["production_method"] = "HOOK_EXTENDED_2ND_VIRAL_VIDEO"
                            logger.info(
                                f"🔥 [HookVideoNode 3-7s Window] Đã ghép tiếp Video Viral #2 vào Cảnh 2 ({dur_s2:.2f}s) "
                                f"-> Tổng thời lượng Hook Video mở đầu = {dur_s1 + dur_s2:.2f}s!"
                            )
                            break
                        else:
                            Path(hook_output_clip_s2).unlink(missing_ok=True)
                except Exception as e_s2:
                    logger.debug(f"[HookVideoNode] Scene 2 viral candidate slice skip: {e_s2}")

            # Ưu tiên 2: Nếu chỉ có 1 ứng viên nhưng đoạn Peak dài >= (dur_s1 + dur_s2 - 0.5s) -> Cắt nối tiếp mạch Peak sang Cảnh 2
            if not s2_filled and natural_peak_dur >= (dur_s1 + min(dur_s2, 1.8)):
                try:
                    cont_src = clean_p_path if (use_precleaned and (get_duration_ms(clean_p_path) / 1000.0) >= (dur_s1 + dur_s2 - 0.35)) else best_v_path
                    cont_offset = dur_s1 if cont_src == clean_p_path else (peak_start + dur_s1)
                    trim_video(
                        input_path=cont_src,
                        duration_seconds=dur_s2,
                        output_path=hook_output_clip_s2,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=atmosphere_fx,
                        apply_grading=True,
                        is_hook_scene=True,
                        fair_use_transform=(cont_src != clean_p_path),
                        start_offset_sec=cont_offset,
                        preserve_continuous_flow=True,
                    )
                    if Path(hook_output_clip_s2).exists() and Path(hook_output_clip_s2).stat().st_size > 10_000:
                        ok_cont, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=2,
                            media_path=hook_output_clip_s2,
                            vid_id=hook_vid_id,
                            url=best_v_path,
                            title=hook_vid_title,
                            is_continuation_slice=True,
                        )
                        if ok_cont:
                            pre_resolved_hook_media[2] = {
                                "media_path": hook_output_clip_s2,
                                "media_type": "hook_ready_video",
                                "production_method": "HOOK_CONTINUOUS_PEAK_SLICE_2",
                                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                                "strategy_rationale": f"Continuous 3-7s Viral Hook slice @{cont_offset:.1f}s (total Hook={dur_s1 + dur_s2:.1f}s)",
                            }
                            if len(scenes_media) >= 2 and isinstance(scenes_media[1], dict):
                                scenes_media[1]["media_path"] = hook_output_clip_s2
                                scenes_media[1]["media_type"] = "hook_ready_video"
                                scenes_media[1]["production_method"] = "HOOK_CONTINUOUS_PEAK_SLICE_2"
                            logger.info(
                                f"🔥 [HookVideoNode 3-7s Window] Đã nối liền mạch đoạn Peak dài sang Cảnh 2 ({dur_s2:.2f}s) "
                                f"-> Tổng thời lượng Hook Video mở đầu = {dur_s1 + dur_s2:.2f}s khớp 100% nhịp thoại!"
                            )
                        else:
                            Path(hook_output_clip_s2).unlink(missing_ok=True)
                except Exception as e_cont:
                    logger.debug(f"[HookVideoNode] Continuous Peak slice to Scene 2 skip: {e_cont}")

        # Ghép thêm đoạn Peak độc lập vào TRONG THÂN VIDEO (tại Cảnh Cao Trào `climax_scene_id`)
        eff_climax_id = climax_scene_id if climax_scene_id not in pre_resolved_hook_media else (3 if len(base_scenes) >= 3 else 0)
        if 1 < eff_climax_id <= len(base_scenes) and eff_climax_id not in pre_resolved_hook_media:
            remaining_passing = [c for c in other_passing if c["video_path"] not in used_second_paths]
            for sec_cand in remaining_passing:
                second_v_path = sec_cand["video_path"]
                sec_clean_p = sec_cand.get("clean_peak_path") or second_v_path
                sec_use_pre = sec_clean_p != second_v_path and Path(sec_clean_p).exists()
                m_yt_sec = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(second_v_path).name)
                sec_vid_id = m_yt_sec.group(1) if m_yt_sec else ""
                sec_title = candidate_titles.get(sec_vid_id) or candidate_titles.get(Path(second_v_path).stem, "")
                if not rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=sec_vid_id,
                    url=second_v_path,
                    title=sec_title,
                    scene_id=eff_climax_id,
                ):
                    continue

                climax_dur_sec = _get_scene_dur_sec(eff_climax_id, default_s=3.5)
                climax_output_clip = str(work_dir / f"hook_climax_scene_{eff_climax_id:03d}.mp4")
                second_peak_start = float(sec_cand.get("peak_offset_sec", 0.0))
                second_peak_dur = float(sec_cand.get("peak_duration_sec") or climax_dur_sec)

                try:
                    trim_video(
                        input_path=sec_clean_p if sec_use_pre else second_v_path,
                        duration_seconds=climax_dur_sec,
                        output_path=climax_output_clip,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=atmosphere_fx,
                        apply_grading=True,
                        is_hook_scene=False,
                        fair_use_transform=not sec_use_pre,
                        start_offset_sec=0.0 if sec_use_pre else second_peak_start,
                        preserve_continuous_flow=True,
                    )
                    if Path(climax_output_clip).exists() and Path(climax_output_clip).stat().st_size > 10_000:
                        ok_climax, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=eff_climax_id,
                            media_path=climax_output_clip,
                            vid_id=sec_vid_id,
                            url=second_v_path,
                            title=sec_title,
                            is_continuation_slice=False,
                        )
                        if not ok_climax:
                            Path(climax_output_clip).unlink(missing_ok=True)
                            continue

                        if sec_vid_id and license_policy.cc_only_enabled():
                            license_policy.record_credit(
                                job_id=job_id,
                                video_id=sec_vid_id,
                                title=sec_title,
                                uploader=candidate_uploaders.get(sec_vid_id, ""),
                            )
                        pre_resolved_hook_media[eff_climax_id] = {
                            "media_path": climax_output_clip,
                            "media_type": "hook_ready_video",
                            "production_method": "CLIMAX_PEAK_SHORT_SPLICE",
                            "visual_strategy": "STOCK_VIDEO",
                            "strategy_rationale": f"Mid-video distinct Peak Short @{second_peak_start:.1f}s (len={second_peak_dur:.1f}s)",
                        }
                        logger.info(
                            f"🔥 [HookVideoNode] Đã cắt video Peak độc lập (@{second_peak_start:.2f}s, len={climax_dur_sec:.2f}s) "
                            f"ghép vào Cảnh Cao Trào #{eff_climax_id} -> {climax_output_clip}"
                        )
                        break
                except Exception as e_climax:
                    logger.debug(f"[HookVideoNode] Không thể cắt đoạn Peak cho Cảnh #{eff_climax_id}: {e_climax}")
    else:
        # CỨU HỘ CẢNH 1 (& CẢNH 2 NẾU < 3.8S) BẰNG VIDEO ĐA NGUỒN (YOUTUBE / PEXELS / PIXABAY / WIKIMEDIA)
        stock_hook_ok = False
        try:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            st_q = queries[0] if queries else (canonical_subject or visual_keyword or topic)
            rescue_subj = (
                f"{beat_1_subject} / {beat_2_subject}"
                if beat_1_subject and beat_2_subject and beat_1_subject.lower() != beat_2_subject.lower()
                else (canonical_subject or primary_entity or topic)
            )
            st_res = execute_youtube_short_peak_clone_sync(
                search_query=st_q,
                output_path=hook_output_clip,
                target_duration_sec=target_hook_duration_sec,
                fallback_query=queries[1] if len(queries) > 1 else topic,
                clip_role="HOOK",
                subject_entity=rescue_subj,
                topic=topic,
                narration=hook_narration,
                search_scope="SCRIPT_OVERALL",
                overall_script_query=topic or st_q,
                job_id=job_id,
                scene_id=1,
                channel_id=channel_id,
                allow_3d_cgi_monster=is_3d_cgi_subject,
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

                # Nếu có full_peak_path dài đủ phủ sang Cảnh 2 (trong cửa sổ Hook 3-7s) -> Nối tiếp mượt mà sang Cảnh 2!
                fp_path = st_res.get("full_peak_path")
                fp_dur = float(st_res.get("full_peak_duration_sec") or 0.0)
                if span_opening_two_scenes and fp_path and Path(fp_path).exists() and fp_dur >= (dur_s1 + min(dur_s2, 1.8)):
                    try:
                        trim_video(
                            input_path=fp_path,
                            duration_seconds=dur_s2,
                            output_path=hook_output_clip_s2,
                            width=1080,
                            height=1920,
                            fps=30,
                            is_scifi=is_scifi,
                            niche=atmosphere_fx,
                            apply_grading=False,
                            is_hook_scene=True,
                            fair_use_transform=False,
                            start_offset_sec=dur_s1,
                            preserve_continuous_flow=True,
                        )
                        if Path(hook_output_clip_s2).exists() and Path(hook_output_clip_s2).stat().st_size > 10_000:
                            pre_resolved_hook_media[2] = {
                                "media_path": hook_output_clip_s2,
                                "media_type": "hook_ready_video",
                                "production_method": "HOOK_CONTINUOUS_PEAK_SLICE_2",
                                "visual_strategy": "STOCK_VIDEO",
                                "strategy_rationale": f"Continuous 3-7s Hook video slice 2 ({dur_s1 + dur_s2:.1f}s total)",
                            }
                            if len(scenes_media) >= 2 and isinstance(scenes_media[1], dict):
                                scenes_media[1]["media_path"] = hook_output_clip_s2
                                scenes_media[1]["media_type"] = "hook_ready_video"
                                scenes_media[1]["production_method"] = "HOOK_CONTINUOUS_PEAK_SLICE_2"
                    except Exception:
                        pass
        except Exception as e_st_hk:
            logger.debug(f"[HookVideoNode] Stock video hook rescue skip: {e_st_hk}")

        if not stock_hook_ok:
            # CHẾ ĐỘ HOOK ĐA ẢNH PEAK LIÊN TỤC (4-5 ẢNH THẬT SỐNG ĐỘNG ĐỔI HÌNH MỖI ~0.85S + LIVE FX)
            final_hook_visual_mode = "scene"
            pose_list = list(scene_1.get("pose_images") or ([existing_path] if existing_path and Path(existing_path).exists() else []))
            if len(pose_list) < 4:
                try:
                    from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                    img_hook_q = queries[0] if queries else (canonical_subject or visual_keyword or topic)
                    out_hook_img = str(hook_cand_dir / "hook_peak_img_001.jpg")
                    img_hook_res = execute_google_cinematic_image_sync(
                        smart_query=img_hook_q,
                        output_path=out_hook_img,
                        narration=hook_narration,
                        topic=topic,
                        is_hook_scene=True,
                        subject_entity=canonical_subject or primary_entity or topic,
                        search_scope="SCRIPT_OVERALL",
                        overall_script_query=img_hook_q,
                        job_id=job_id,
                        scene_id=1,
                        target_image_count=5,
                    )
                    if img_hook_res and img_hook_res.get("pose_images"):
                        pose_list = list(img_hook_res["pose_images"])
                except Exception as e_img_hk:
                    logger.debug(f"[HookVideoNode] Multi-image peak hook fetch skip: {e_img_hk}")

            if pose_list:
                num_cuts = max(3, len(pose_list) - 1)
                logger.info(
                    f"⚡ [HookVideoNode] Dựng Hook Đa Ảnh Peak Sống Động ({len(pose_list)} ảnh thật thay đổi liên tục mỗi "
                    f"{target_hook_duration_sec / max(len(pose_list), 1):.2f}s + 3D Motion & Live FX) -> {hook_output_clip}"
                )
                render_opening_hook_multi_cut(
                    image_paths=pose_list,
                    duration_seconds=target_hook_duration_sec,
                    output_path=hook_output_clip,
                    width=1080,
                    height=1920,
                    fps=30,
                    min_cuts=num_cuts,
                    is_scifi=is_scifi,
                    niche=atmosphere_fx,
                    is_hook_scene=True,
                )
                if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
                    hook_mode = f"CLIMAX_IMAGES_LIVE_FX_{atmosphere_fx.upper()}_{len(pose_list)}SHOTS"
                    pre_resolved_hook_media[1] = {
                        "media_path": hook_output_clip,
                        "media_type": "hook_ready_video",
                        "pose_images": pose_list,
                        "production_method": hook_mode,
                        "visual_strategy": "HOOK_MULTI_IMAGE_RAPID_PEAK",
                        "strategy_rationale": f"Rapid {len(pose_list)}-image Peak Hook with 3D motion & Live FX",
                    }
                    scene_1["media_path"] = hook_output_clip
                    scene_1["media_type"] = "hook_ready_video"
                    scene_1["production_method"] = hook_mode
                    if scenes_media:
                        scenes_media[0] = scene_1
                else:
                    hook_mode = f"TRADITIONAL_SCENE_FALLBACK_{atmosphere_fx.upper()}"
            else:
                logger.info(
                    "⚡ [HookVideoNode] Các ứng viên Short không đạt chuẩn -> Gemini chuyển Hook sang chế độ Tạo Cảnh ('scene') tại fetch_media!"
                )
                hook_mode = f"TRADITIONAL_SCENE_FALLBACK_{atmosphere_fx.upper()}"

    actual_hook_window_sec = round(
        (dur_s1 + dur_s2) if (1 in pre_resolved_hook_media and 2 in pre_resolved_hook_media) else target_hook_duration_sec,
        2,
    )
    eval_summary = {
        "mode": hook_mode,
        "hook_production_strategy": hook_production_strategy,
        "visual_hook_blueprint": visual_hook_blueprint,
        "beat_1_query": beat_1_query,
        "beat_2_query": beat_2_query,
        "hook_visual_mode": final_hook_visual_mode,
        "is_abstract_keyword": is_abstract_keyword,
        "climax_scene_id": climax_scene_id,
        "climax_description": climax_desc,
        "search_queries": queries,
        "atmosphere_fx": atmosphere_fx,
        "candidates_tested": len(scored_candidates),
        "best_score": best_candidate["overall_score"] if best_candidate else (8.4 if pre_resolved_hook_media else None),
        "best_scroll_stop_score": best_candidate.get("scroll_stop_score") if best_candidate else None,
        "best_reason": best_candidate["reason"] if best_candidate else ("Rapid Multi-Image Peak Hook / Stock Motion Spliced" if pre_resolved_hook_media else "Delegated to fetch_media & VisualCritic (scene mode)"),
        "evaluated_by": "vision_ai" if best_candidate else ("peak_synthesizer" if pre_resolved_hook_media else "delegated_to_visual_critic"),
        "output_clip": hook_output_clip if Path(hook_output_clip).exists() else None,
        "spliced_scenes": list(pre_resolved_hook_media.keys()),
        "duration_sec": actual_hook_window_sec,
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
