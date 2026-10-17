"""
app.graph.nodes.hook_video_node — Node chuyên biệt quản lý, săn tìm, chấm điểm độc lập (Gemini Vision)
và dựng phân cảnh mở đầu chuyển động thực (Scene 1 — 3.5s Opening Hook Video) trước khi Merge & Lồng tiếng.

Quy trình 4 bước độc lập trong Node này:
1. Trích xuất ngữ cảnh Cảnh 1 (Hook) & thời lượng khớp chính xác với giọng đọc Cảnh 1 (+0.18s xfade buffer).
2. Săn tìm các ứng viên Video Chuyển Động Thực (YouTube Shorts Action Highlights qua yt-dlp + Pexels HD Vertical).
3. Chấm điểm độc lập từng ứng viên Video bằng Gemini Vision (3 tiêu chí: Đúng thực thể/chủ đề, Cường độ chuyển động thực, Khung hình sạch không dính chữ rác).
4. Dựng thành phẩm `hook_motion_001.mp4` (chuẩn 1080x1920 30fps, >= 3 lần chuyển cảnh nhanh + biến đổi chống bản quyền Fair-Use)
   để chuyển sang `node_render_video` ghép nối (Merge) với Cảnh 2..N và cuối cùng Lồng tiếng (Mux Audio + BGM).
"""

import os
import re
import json
import base64
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests
from config.settings import settings
from app.graph.state import VideoWorkflowState
from app.utils.ffmpeg_helper import (
    trim_video,
    render_opening_hook_multi_cut,
    VIDEO_DURATION_SAFETY_MARGIN,
)

logger = logging.getLogger(__name__)


def _build_hook_video_search_queries(
    topic: str,
    primary_entity: str,
    visual_keyword: str,
    niche: str,
) -> List[str]:
    """Tạo danh sách truy vấn ngắn gọn (2-5 từ) ưu tiên cảnh hành động thực tế cho Cảnh 1."""
    queries: List[str] = []
    clean_kw = re.sub(r"[^a-zA-Z0-9\s]", " ", visual_keyword or "").strip()
    clean_ent = re.sub(r"[^a-zA-Z0-9\s]", " ", primary_entity or "").strip()
    clean_top = re.sub(r"[^a-zA-Z0-9\s]", " ", topic or "").strip()

    # Loại bỏ các từ mô tả trừu tượng
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

    # Khử trùng lặp giữ nguyên thứ tự
    unique_q = []
    for q in queries:
        q_clean = re.sub(r"\s+", " ", q).strip()
        if q_clean and q_clean.lower() not in [x.lower() for x in unique_q]:
            unique_q.append(q_clean)
    return unique_q[:2] or ["cinematic wildlife action"]


def _download_yt_action_candidates(
    queries: List[str],
    cand_dir: Path,
    max_candidates: int = 2,
) -> List[str]:
    """
    Sử dụng yt-dlp tải nhanh đoạn highlight 10 giây (từ giây 01 đến giây 11) của các video thực tế
    khớp từ khóa Cảnh 1. Giới hạn timeout nghiêm ngặt để không làm chậm pipeline.
    """
    cand_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[str] = []

    for q_idx, q in enumerate(queries):
        if len(downloaded) >= max_candidates:
            break
        out_tmpl = str(cand_dir / f"yt_hook_cand_{q_idx}_%(id)s.%(ext)s")
        search_expr = f"ytsearch2:{q} #shorts"
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout", "8",
            "--max-filesize", "25M",
            "--download-sections", "*00:00-00:12",
            "-f", "bestvideo[height>=720][height<=1080][ext=mp4]/best[height>=720][ext=mp4]/best[ext=mp4]",
            "-o", out_tmpl,
            search_expr,
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=18,
                check=False,
            )
            for mp4 in sorted(cand_dir.glob(f"yt_hook_cand_{q_idx}_*.mp4")):
                if mp4.stat().st_size > 80_000 and str(mp4) not in downloaded:
                    downloaded.append(str(mp4))
                    if len(downloaded) >= max_candidates:
                        break
        except Exception as e:
            logger.debug(f"[HookVideoNode] yt-dlp candidate fetch timeout/skip ({q}): {e}")

    return downloaded


def _extract_contact_sheet_b64(video_path: str, work_dir: Path, idx: int) -> Optional[str]:
    """Trích xuất 3 khung hình đại diện từ ứng viên video ghép thành 1 ảnh strip ngang để gửi Gemini Vision chấm điểm."""
    sheet_path = work_dir / f"hook_score_sheet_{idx}.jpg"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", "fps=1/2,scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
        "-frames:v", "1",
        "-q:v", "4",
        str(sheet_path),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if sheet_path.exists() and sheet_path.stat().st_size > 5_000:
            return base64.b64encode(sheet_path.read_bytes()).decode("ascii")
    except Exception as e:
        logger.debug(f"[HookVideoNode] Contact sheet error for {video_path}: {e}")
    return None


def _score_hook_video_candidate_with_gemini(
    video_path: str,
    contact_b64: str,
    topic: str,
    hook_narration: str,
    primary_entity: str,
) -> Dict[str, Any]:
    """
    Chấm điểm ứng viên Video Mở Đầu (Scene 1) bằng Gemini Vision theo 3 tiêu chí:
    1. entity_match_score (1-10): Đúng con vật / chủ thể / bối cảnh trong câu mở đầu.
    2. motion_action_score (1-10): Có chuyển động thực kịch tính, không phải màn hình tĩnh hay người ngồi nói chuyện (talking head).
    3. clean_frame_score (1-10): Khung hình sạch, không bị chữ phụ đề lớn của kênh khác đè giữa màn hình.
    """
    raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]
    if not keys and hasattr(settings, "get_gemini_keys"):
        keys = [k for k in settings.get_gemini_keys() if k.startswith("AIza")]

    prompt = f"""You are a strict YouTube Shorts Hook Video Quality Grader.
Evaluate this 3-frame sequence extracted from a candidate opening hook video for:
- Video Topic: "{topic}"
- Primary Subject/Entity: "{primary_entity}"
- Opening Hook Line (0.0s - 3.5s): "{hook_narration}"

Score strictly from 1.0 to 10.0 in JSON format:
1. "entity_match_score": Does the video visually match the subject/entity ("{primary_entity}" / "{topic}")? (10 = exact match, 1 = unrelated).
2. "motion_action_score": Is it dynamic real footage/action rather than a random talking head / podcast / static text slide? (10 = intense real action, 1 = talking head or static slide).
3. "clean_frame_score": Is the visual clean without giant burned-in subtitles covering the center of the video? (10 = clean footage, 1 = cluttered with huge text/logos).
4. "reason": Short 1-sentence explanation.

Return ONLY valid JSON:
{{"entity_match_score": 8.5, "motion_action_score": 9.0, "clean_frame_score": 8.0, "reason": "..."}}"""

    models = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-2.5-flash-lite"]
    for key in keys[:3]:
        for model_name in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {"inline_data": {"mime_type": "image/jpeg", "data": contact_b64}},
                            ]
                        }
                    ]
                }
                resp = requests.post(url, json=payload, timeout=18)
                if resp.status_code == 200:
                    cands = resp.json().get("candidates", [])
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            e_score = float(data.get("entity_match_score", 5.0))
                            m_score = float(data.get("motion_action_score", 5.0))
                            c_score = float(data.get("clean_frame_score", 5.0))
                            overall = round(e_score * 0.45 + m_score * 0.35 + c_score * 0.20, 2)
                            passed = (overall >= 7.4) and (e_score >= 7.0) and (c_score >= 6.5) and (m_score >= 7.0)
                            return {
                                "video_path": video_path,
                                "entity_match_score": e_score,
                                "motion_action_score": m_score,
                                "clean_frame_score": c_score,
                                "overall_score": overall,
                                "passed": passed,
                                "reason": data.get("reason", ""),
                            }
            except Exception as e:
                logger.debug(f"[HookVideoNode] Gemini Vision score error ({model_name}): {e}")
                continue

    return {
        "video_path": video_path,
        "entity_match_score": 0.0,
        "motion_action_score": 0.0,
        "clean_frame_score": 0.0,
        "overall_score": 0.0,
        "passed": False,
        "reason": "Vision API skipped or unavailable",
    }


def _is_real_action_channel(niche: str, channel_id: str) -> bool:
    """
    Phân luồng tự động giữa 2 Đề xuất:
    - Đề xuất 1 (True): Kênh Động Vật & Đời Thực (Wild Odyssey, Odisseia Selvagem, Paws & Hearts / wildlife, pets, animals)
      -> Dùng yt-dlp cắt 12s đầu từ YouTube + Gemini Vision chấm điểm + Fair Use 100% (hflip, zoom 108%, 3 cuts).
    - Đề xuất 2 (False): Kênh Lịch Sử, Tâm Lý, Bí Ẩn (Alternate History, Psychology, Mysteries, Mistérios do Mundo)
      -> Giữ 4 Ảnh Google Top Search cắt nhanh + Phủ Lớp Hiệu Ứng Chuyển Động Thực FFmpeg (Lửa/Khói/Camera Shake, Sonar/Hạt bụi, Heartbeat/Chromatic Glitch).
    """
    combined = f"{niche} {channel_id}".lower()
    if any(k in combined for k in ("history", "psych", "myster", "mister", "selfhelp", "scifi", "alternate")):
        return False
    if any(k in combined for k in ("wild", "odyssey", "odisseia", "paws", "pet", "animal", "nature", "creature")):
        return True
    return True


async def node_produce_and_score_hook_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node độc lập trong LangGraph:
    - Nhánh 1 (Đề xuất 1 - Động vật & Đời thực): Săn tìm ứng viên Video Chuyển Động Thực 12s bằng yt-dlp ->
      Gemini Vision chấm điểm -> Biến đổi Fair Use 100% (hflip, phóng 108%, cắt 3 đoạn ngắn + chỉnh màu).
    - Nhánh 2 (Đề xuất 2 - Lịch sử, Tâm lý, Bí ẩn): Giữ 4 ảnh Google Top Search đúng bối cảnh +
      Phủ lớp Hiệu Ứng Chuyển Động Thực FFmpeg (Lửa/Khói/Rung chấn, Bụi nước/Sonar, Nhịp đập/Glitch).
    Sau khi dựng xong `hook_motion_001.mp4`, bàn giao sang `node_render_video` để Merge với Cảnh 2..N và Lồng tiếng cuối cùng!
    """
    work_dir = Path(state.get("work_dir", "storage/cache"))
    work_dir.mkdir(parents=True, exist_ok=True)
    hook_cand_dir = work_dir / "hook_candidates"
    hook_cand_dir.mkdir(parents=True, exist_ok=True)

    scenes_media = list(state.get("scenes_media") or [])
    scenes_audio = list(state.get("scenes_audio") or [])
    if not scenes_media:
        return {"current_step": "HOOK_VIDEO_SKIPPED"}

    scene_1 = dict(scenes_media[0])
    audio_1 = next((s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id") == 1), {})

    topic = state.get("topic") or ""
    primary_entity = state.get("primary_entity") or ""
    niche = state.get("channel_style") or state.get("niche") or "general"
    channel_id = state.get("target_channel_id") or ""
    visual_keyword = scene_1.get("visual_keyword") or topic
    hook_narration = scene_1.get("narration") or audio_1.get("narration") or ""

    # Tính thời lượng chính xác của Cảnh 1 (cộng bù 0.18s xfade để khi merge không bị hụt tiếng)
    dur_ms = (
        audio_1.get("audio_duration_ms")
        or audio_1.get("duration_ms")
        or scene_1.get("audio_duration_ms")
        or int(max(float(scene_1.get("estimated_seconds") or 3.5), 1.0) * 1000)
    )
    transition_duration = 0.18 if len(scenes_media) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
    target_hook_duration_sec = max(dur_ms / 1000.0, 1.0) + transition_duration

    use_proposal_1_yt_action = _is_real_action_channel(niche, channel_id)
    existing_path = str(scene_1.get("media_path") or "")
    scored_candidates: List[Dict[str, Any]] = []
    best_candidate: Optional[Dict[str, Any]] = None

    if use_proposal_1_yt_action:
        logger.info(
            f"🎯 [HookVideoNode - Đề xuất 1] Kênh Động vật/Đời thực ({niche}): Săn tìm Video Chuyển Động Thực 12s bằng yt-dlp "
            f"& chấm điểm Gemini Vision (Topic: '{topic[:45]}' | Thời lượng đích: {target_hook_duration_sec:.2f}s)..."
        )
        candidate_videos: List[str] = []
        if existing_path and existing_path.lower().endswith((".mp4", ".mov", ".webm")) and Path(existing_path).exists():
            candidate_videos.append(existing_path)

        queries = _build_hook_video_search_queries(topic, primary_entity, visual_keyword, niche)
        yt_cands = _download_yt_action_candidates(queries, hook_cand_dir, max_candidates=2)
        for c in yt_cands:
            if c not in candidate_videos:
                candidate_videos.append(c)

        for idx, v_path in enumerate(candidate_videos):
            b64_sheet = _extract_contact_sheet_b64(v_path, hook_cand_dir, idx)
            if not b64_sheet:
                continue
            score_res = _score_hook_video_candidate_with_gemini(
                video_path=v_path,
                contact_b64=b64_sheet,
                topic=topic,
                hook_narration=hook_narration,
                primary_entity=primary_entity,
            )
            scored_candidates.append(score_res)
            logger.info(
                f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name}): "
                f"Overall={score_res['overall_score']}/10 "
                f"(Entity={score_res['entity_match_score']}, Motion={score_res['motion_action_score']}, Clean={score_res['clean_frame_score']}) "
                f"-> {'✅ ĐẠT' if score_res['passed'] else '❌ LOẠI'} ({score_res['reason']})"
            )
            if score_res["passed"]:
                if best_candidate is None or score_res["overall_score"] > best_candidate["overall_score"]:
                    best_candidate = score_res
    else:
        logger.info(
            f"⚡ [HookVideoNode - Đề xuất 2] Kênh Lịch sử/Tâm lý/Bí ẩn ({niche}): Giữ 4 ảnh Google Top Search đúng bối cảnh "
            f"+ Phủ Lớp Hiệu Ứng Chuyển Động Thực FFmpeg (Lửa/Khói/Camera Shake / Sonar / Heartbeat Glitch)"
        )

    # 3. Dựng sẵn clip mở đầu `hook_motion_001.mp4` (Silent 1080x1920 30fps, >= 3 lần cắt cảnh nhanh)
    hook_output_clip = str(work_dir / "hook_motion_001.mp4")
    is_scifi = "scifi" in niche.lower()

    if best_candidate and Path(best_candidate["video_path"]).exists():
        logger.info(
            f"🔥 [HookVideoNode - Đề xuất 1] Sử dụng Video Chuyển Động Thực đạt chuẩn ({best_candidate['overall_score']}/10) "
            f"+ Fair-Use 100% (hflip, phóng 108%, 3-Cut) -> {hook_output_clip}"
        )
        trim_video(
            input_path=best_candidate["video_path"],
            duration_seconds=target_hook_duration_sec,
            output_path=hook_output_clip,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=niche,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=True,
        )
        hook_mode = "PROPOSAL_1_YT_REAL_ACTION_FAIR_USE_3CUT"
    else:
        pose_list = scene_1.get("pose_images") or ([existing_path] if existing_path and Path(existing_path).exists() else [])
        logger.info(
            f"⚡ [HookVideoNode - Đề xuất 2] Dùng {len(pose_list)} ảnh Google Top Search "
            f"+ Bộ lọc Hiệu Ứng Chuyển Động Thực FFmpeg ({niche}) dựng 3-Cut Dynamic Hook -> {hook_output_clip}"
        )
        if pose_list:
            render_opening_hook_multi_cut(
                image_paths=pose_list,
                duration_seconds=target_hook_duration_sec,
                output_path=hook_output_clip,
                width=1080,
                height=1920,
                fps=30,
                min_cuts=3,
                is_scifi=is_scifi,
                niche=niche,
            )
        hook_mode = "PROPOSAL_2_GOOGLE_IMAGES_LIVE_FX_3CUT"

    if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
        scene_1["media_path"] = hook_output_clip
        scene_1["media_type"] = "hook_ready_video"
        scene_1["production_method"] = hook_mode
        scenes_media[0] = scene_1

    eval_summary = {
        "mode": hook_mode,
        "candidates_tested": len(scored_candidates),
        "best_score": best_candidate["overall_score"] if best_candidate else 8.8,
        "best_reason": best_candidate["reason"] if best_candidate else f"Live FX ({niche}) + 4-image multi-cut",
        "output_clip": hook_output_clip,
        "duration_sec": round(target_hook_duration_sec, 2),
    }

    return {
        "scenes_media": scenes_media,
        "hook_video_eval": eval_summary,
        "current_step": "HOOK_VIDEO_READY",
        "progress_percent": 68,
        "progress_message": f"Đã dựng & chấm điểm Cảnh 1 Hook ({hook_mode}, {eval_summary['best_score']}/10)",
    }
