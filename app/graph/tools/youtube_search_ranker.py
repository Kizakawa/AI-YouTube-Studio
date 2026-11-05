"""app.graph.tools.youtube_search_ranker — Tìm kiếm YouTube Shorts, LLM Candidate Selector & Gemini Vision Gate."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)
from app.graph.tools.tool_schemas import (
    _yt_url_lock,
    _yt_search_sem,
    _yt_flat_search_cache,
    _used_short_windows,
    _TALKING_HEAD_BLOCKLIST,
    _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST,
    _YT_QUERY_FILLER_WORDS,
    is_edit_or_ai_slop_video_metadata,
)


def _find_best_peak_window(
    info: Dict[str, Any],
    clip_dur: float,
    role: str = "PEAK",
    existing_windows: Optional[List[Tuple[float, float]]] = None,
    max_peak_dur: float = 14.0,
) -> Optional[Tuple[float, float]]:
    """
    Xác định mốc thời gian (start_sec, end_sec) đắt giá nhất trong chiếc YouTube Short.
    KHÔNG KHÓA CỨNG 3 GIÂY:
    - Nếu đoạn Peak (Most Replayed heatmap hoặc cao trào hành động) kéo dài liên tục (ví dụ 6s - 14s),
      ưu tiên lấy TRỌN VẸN cả đoạn Peak dài đó (`max_peak_dur`, mặc định tới 14s) để ghép liền mạch
      qua nhiều phân cảnh liên tiếp thay vì cắt cụt ở 3 giây!
    - Đảm bảo KHÔNG TRÙNG LẶP với các khoảng `existing_windows` nếu cùng 1 video tổng thể được cắt
      nhiều phân đoạn khác nhau trong cùng 1 kịch bản.
    """
    total_dur = float(info.get("duration") or 15.0)
    min_dur = max(3.0, min(clip_dur, 8.0))
    max_dur = max(min_dur + 1.0, min(max_peak_dur, max(3.5, total_dur - 0.8)))
    used_wins = existing_windows or []

    def _overlaps(s: float, e: float) -> bool:
        for us, ue in used_wins:
            if max(s, us) < min(e, ue) - 0.4:
                return True
        return False

    if total_dur <= min_dur + 1.0:
        if used_wins:
            return None
        return 0.0, max(2.0, total_dur)

    # 1. Thử lấy từ đỉnh Most Replayed (heatmap) — GỘP LIỀN MẠCH TOÀN BỘ VÙNG PEAK DÀI NẾU ĐỈNH KÉO DÀI
    heatmap = info.get("heatmap")
    if isinstance(heatmap, list) and len(heatmap) >= 3:
        indexed_pts = [(idx, pt) for idx, pt in enumerate(heatmap) if isinstance(pt, dict)]
        sorted_pts = sorted(indexed_pts, key=lambda x: float(x[1].get("value", 0.0)), reverse=True)
        for best_idx, pt in sorted_pts[:4]:
            peak_val = float(pt.get("value", 0.0))
            if peak_val > 0.22:
                # Mở rộng sang trái & phải trên heatmap chừng nào độ cuốn hút vẫn cao (>= 52% đỉnh hoặc >= 0.25)
                cont_thresh = max(0.22, peak_val * 0.52)
                left_idx = best_idx
                right_idx = best_idx
                while left_idx > 0 and float(heatmap[left_idx - 1].get("value", 0.0)) >= cont_thresh:
                    if float(heatmap[right_idx].get("end_time", 0.0)) - float(heatmap[left_idx - 1].get("start_time", 0.0)) > max_dur:
                        break
                    left_idx -= 1
                while right_idx + 1 < len(heatmap) and float(heatmap[right_idx + 1].get("value", 0.0)) >= cont_thresh:
                    if float(heatmap[right_idx + 1].get("end_time", 0.0)) - float(heatmap[left_idx].get("start_time", 0.0)) > max_dur:
                        break
                    right_idx += 1

                raw_start = float(heatmap[left_idx].get("start_time", 0.0))
                raw_end = float(heatmap[right_idx].get("end_time", 0.0))
                span_len = raw_end - raw_start
                eff_len = max(min_dur, min(max_dur, span_len if span_len >= min_dur else max(min_dur, 8.5)))
                peak_center = (raw_start + raw_end) / 2.0
                start_s = max(0.4, min(raw_start if span_len >= min_dur else (peak_center - eff_len / 2.0), total_dur - eff_len - 0.2))
                end_s = min(total_dur, start_s + eff_len)
                if not _overlaps(start_s, end_s):
                    return round(start_s, 2), round(end_s, 2)

    # 2. Các mốc ứng viên theo vai trò: Cho phép cửa sổ rộng (tới min(max_dur, 11.5s)) để bộ quét CV lấy trọn đoạn Peak dài
    probe_span = min(max_dur, max(min_dur, min(11.5, total_dur * 0.65)))
    candidate_ratios = [0.05, 0.28, 0.52, 0.70] if (role or "").upper() == "HOOK" else [0.25, 0.50, 0.12, 0.68]
    for ratio in candidate_ratios:
        start_s = 0.6 if ratio <= 0.08 else max(0.8, min(total_dur * ratio, total_dur - min_dur - 0.3))
        end_s = min(total_dur, start_s + probe_span)
        if end_s - start_s >= min_dur and not _overlaps(start_s, end_s):
            return round(start_s, 2), round(end_s, 2)

    return None


def _verify_short_clip_with_gemini_vision(
    video_path: str,
    topic: str,
    narration: str,
    subject_entity: str,
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    video_title: str = "",
    allow_3d_cgi_monster: bool = False,
    strict_no_fallback: bool = True,
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
    target_visual_query: str = "",
) -> Tuple[bool, float, str]:
    """
    Trích xuất 3 khung hình (tile=3x1) từ đoạn clip vừa cắt và nhờ Gemini Vision kiểm duyệt nghiêm ngặt:
    - Chặn 100% clip có watermark, logo kênh khác (LatestSightings, MR. FRIEND, @username), phụ đề TikTok/Shorts in cứng, hoặc chữ bị lật ngược.
    - Chặn clip mờ, vỡ hạt, lệch màu, tranh vẽ trộn lẫn cảnh thật, hoặc sai môi trường sống (vd: báo trong sở thú tuyết khi truyện ở thảo nguyên).
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, 0.0, "Cancelled by parent task"
    if deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.5:
        return False, 0.0, "Deadline exceeded before vision check"
    if not Path(video_path).exists():
        return False, 0.0, "File không tồn tại"

    from app.graph.evaluators.visual_critic import VisualCritic
    # Giao phần kiểm tra thông số kỹ thuật vật lý (độ phân giải >=720p, độ nét >=5.4, độ sáng, cắt viền đen/chớp đen, chuyển động thật)
    # cho CV Gate; còn phần nhận diện chữ/logo/watermark và ngữ nghĩa chủ thể do Multimodal Gemini Vision quyết định trực tiếp bên dưới
    # (tránh việc bộ đếm cạnh tương phản trên cỏ khô thảo nguyên / vảy rắn báo nhầm là caption box trước khi gọi Gemini Vision).
    pre_gate = VisualCritic.inspect_clip_before_concat(
        video_path,
        min_short_edge=720,
        min_sharpness=5.4,
        check_watermark=False,
        allow_snow=True,
        auto_sanitize=True,
    )
    if not pre_gate.get("passed", False):
        return False, float(pre_gate.get("score", 2.0)), "; ".join(pre_gate.get("issues") or ["Failed pre-assembly clip gate"])
    if float(pre_gate.get("brightness", 50.0)) < 34.0:
        return False, 2.5, f"Clip quá tối / thiếu chi tiết chủ thể (brightness={pre_gate.get('brightness', 0):.1f} < 34)"
    try:
        from app.graph.nodes.media.hook_video_helpers import verify_hook_clip_genuine_motion
        ok_gen_mot, mot_sc, mot_rsn = verify_hook_clip_genuine_motion(video_path)
        if not ok_gen_mot:
            return False, 2.0, f"Clip là ảnh tĩnh phóng to Ken-Burns / đứng hình ({mot_rsn}, score={mot_sc})"
    except Exception:
        pass
    if cancel_event is not None and cancel_event.is_set():
        return False, 0.0, "Cancelled by parent task after CV pre-gate"

    with tempfile.TemporaryDirectory() as tmp_v:
        sheet_jpg = str(Path(tmp_v) / "verify_sheet.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "fps=1,scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
            "-frames:v", "1", "-q:v", "4",
            sheet_jpg,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=8, check=False)
        except Exception as e:
            return (False, 0.0, f"Sheet extract error ({e})") if strict_no_fallback else (True, 7.2, f"Skip sheet extract ({e})")

        if not Path(sheet_jpg).exists() or Path(sheet_jpg).stat().st_size < 3000:
            return (False, 0.0, "Sheet missing") if strict_no_fallback else (True, 7.2, "Sheet fallback")

        b64_img = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

    try:
        import requests
        from app.utils.key_rotator import KeyRotator

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if not rotator or not rotator.has_keys:
            return (False, 0.0, "No Gemini keys for strict vision check") if strict_no_fallback else (True, 7.5, "No Gemini keys")

        cgi_rule = (
            "3. 2D kids cartoon / chibi / Minecraft / Roblox / plastic toy / GMod (NOTE: Clean watermark-free photorealistic 3D CGI / Unreal Engine 5 / Blender / Thalassophobia underwater animation and dark murky abyssal silhouette scenes ARE APPROVED for this monster scene if they have ZERO watermarks, ZERO channel names, and ZERO burned-in subtitles!)\n"
            "6. Normal real-world tourist boats or weather towers in bright sunny daylight with zero monster/underwater tension"
            if allow_3d_cgi_monster
            else "3. 2D drawings, paintings, illustrations, cartoons, anime, video games, CGI renders, OR AI-generated synthetic videos (e.g., Sora/Veo/Kling/Runway/Hailuo AI animals with airbrushed/plastic CGI fur/feathers, artificial golden rim-lighting, symmetrical slow-motion CGI flight straight at camera, unnatural glowing eyes, or hybrid owl-eagle facial anatomy) mixed into a live-action documentary"
        )

        eff_target_query = target_visual_query or overall_script_query or subject_entity or topic
        scope_note = (
            f'NOTE: This scene line is part of an overall story ("{overall_script_query or topic}") with Directed Visual Query "{eff_target_query}".\n'
            f'- DUAL-SUBJECT ("A vs B") RULE: When the story involves two entities (e.g. "Entity A vs Entity B"), individual B-roll clips often show JUST Entity A, JUST Entity B, or both interacting. APPROVE clean HD footage that clearly shows the target subject in "{eff_target_query}" OR either primary entity from "{subject_entity or topic}" in its natural habitat!\n'
            f'- METAPHOR / ANATOMY B-ROLL RULE: If the narration uses a metaphor or internal mechanism (e.g., "like car tires", "like a shield", "heart pumps like a racing engine"), it is illustrated externally by real footage matching "{eff_target_query}" (e.g. the animal running, panting, spreading wings, or striking)—APPROVE clean real footage of the animal!'
        )

        prompt = f"""You are a Strict Copyright, Watermark & Multimodal Script-Context Inspector for a professional YouTube Shorts studio.
Look carefully at all 3 frames in this strip extracted from a candidate video clip:
- Overall Script Topic: "{topic}"
- Core Story Theme & Habitat: "{overall_script_query or topic}"
- Main Canonical Subject: "{subject_entity or topic}"
- Directed Visual Query for this Scene: "{eff_target_query}"
- Current Scene Line ({search_scope}): "{narration}"
- Source Video Title: "{video_title}"

{scope_note}
- CROSS-SCENE PRONOUN & EPITHET RESOLUTION: If `Current Scene Line` uses pronouns ("It", "They") or generic role nouns ("the predator", "the hunter", "its living prey"), resolve them back to the exact canonical species/entities in `Overall Script Topic` ("{topic}") and `Main Canonical Subject` ("{subject_entity or topic}").

SCORE 3 AXES INDEPENDENTLY (0.0–10.0) AND REJECT IMMEDIATELY (passed=false, score < 4.0) IF YOU SEE ANY OF THESE IN ANY FRAME:
1. `visual_integrity_score`: REJECT (`< 4.0`) if you see ANY channel watermark, logo, corner bug, AI generator corner badge/sparkle icon (CHECK ALL 4 CORNERS CLOSELY — especially a small white/translucent 4-point diamond/star sparkle icon `✦` in the bottom-right corner from Gemini/Veo, or Sora/Kling/Runway/Pika/CapCut badges), username (@...), credit banner, burned-in TikTok/Shorts subtitle/caption, or mirrored/flipped text anywhere on the frame (e.g., "LatestSightings", "MR. FRIEND", TV logos, social handles, yellow/white captions), OR blurry/pixelated/washed-out quality.
{cgi_rule}
4. `narrative_action_score`: REJECT (`< 4.0`) if there is a habitat or environment mismatch with the script (e.g., an animal in a snowy zoo enclosure, behind cage bars/fences, wearing falconry jesses/anklet straps/leashes, with a human handler, or indoors when the story takes place in the wild rainforest, savanna, or ocean).
5. `entity_identity_score`: REJECT (`< 4.0`) if the clip shows the wrong animal/subject species that matches neither "{eff_target_query}" nor the canonical species in "{subject_entity or topic}" (e.g., a different eagle/bird species from another continent just because it shares the generic word "eagle" or "bird"), or a talking head / vlog / product commercial.
6. Bad framing / severely cropped subject where only a tiny unrecognizable corner, tail, or cut-off edge of the main subject fits inside the 9:16 vertical frame instead of a clear, well-composed view of the subject.
7. ANTI-SLIDESHOW / 2D KEN-BURNS STILL PHOTO & COMPOSITE GATE: Compare the 3 frames (LEFT -> CENTER -> RIGHT). If the 3 frames are a static photograph with only a 2D zoom/pan (Ken-Burns effect) or a static cut-out composite where the primary subject's 3D body posture, limbs/talons/wings, head angle, and feathers remain frozen in the exact same pose across all 3 frames, REJECT IMMEDIATELY (`narrative_action_score < 2.0`, `passed: false`). Real video footage MUST show genuine 3D physical movement across the 3 frames!

Set `score = min(entity_identity_score, narrative_action_score, visual_integrity_score)` and `passed = true` ONLY when all 3 axes are `>= 7.2`.
Return ONLY valid JSON:
{{"passed": true, "entity_identity_score": 8.5, "narrative_action_score": 8.5, "visual_integrity_score": 8.5, "score": 8.5, "reason": "100% clean HD footage with zero watermarks/AI corner icons/text matching canonical subject and habitat"}}"""

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                ]
            }],
            "generationConfig": {"temperature": 0.1},
        }

        from app.agents.curation.scene_context_analyzer_mixin import _VISION_SEM
        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
        vision_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
        # Đảm bảo bước gọi HTTP Gemini Vision luôn có tối thiểu 18s tính từ sau khi trích xuất xong verify_sheet.jpg
        vision_http_deadline = max(deadline_ts or 0.0, time.monotonic() + 18.0)
        for model_name, api_key in rotator.iter_best_model_key_pairs(vision_models, max_total_attempts=8):
            if cancel_event is not None and cancel_event.is_set():
                return False, 0.0, "Cancelled by parent task"
            if (vision_http_deadline - time.monotonic()) <= 2.0:
                break
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            gen_cfg = KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1)
            req_payload = {
                "contents": payload["contents"],
                "generationConfig": gen_cfg,
            }
            sem_wait = min(15.0, max(0.5, vision_http_deadline - time.monotonic() - 2.0))
            if not _VISION_SEM.acquire(timeout=sem_wait):
                break
            try:
                if (cancel_event is not None and cancel_event.is_set()) or (
                    (vision_http_deadline - time.monotonic()) <= 1.5
                ):
                    break
                eff_read = min(10.0, max(3.8, vision_http_deadline - time.monotonic() - 0.5))
                t_req0 = time.monotonic()
                resp = requests.post(url, json=req_payload, timeout=(3.5, eff_read))
                if resp.status_code == 200:
                    rotator.record_model_success(api_key, model_name, time.monotonic() - t_req0)
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
                            raw_sc = float(data.get("score", 0.0))
                            ent_s = float(data.get("entity_identity_score", raw_sc))
                            act_s = float(data.get("narrative_action_score", raw_sc))
                            int_s = float(data.get("visual_integrity_score", raw_sc))
                            score = round(min(raw_sc, ent_s, act_s, int_s), 2)
                            passed = bool(data.get("passed", False)) and score >= 7.2
                            reason = str(data.get("reason", ""))
                            return passed, score, reason
                elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
            except Exception as req_ex:
                err_s_v = str(req_ex)
                if "Read timed out" not in err_s_v and "timed out" not in err_s_v.lower():
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=err_s_v[:160])
                continue
            finally:
                _VISION_SEM.release()
    except Exception as ex:
        logger.debug(f"[YouTubePeakTool] Vision check fallback: {ex}")

    if strict_no_fallback:
        return False, 0.0, "Strict vision gate: rejected unverified clip"

    # Chỉ chạy đến đây khi strict_no_fallback=False và TẤT CẢ các model Gemini Vision đều bị lỗi mạng/429/503.
    # Khi đó, kiểm tra nghiêm ngặt bằng VisualCritic (bật check_watermark=True) + đối chiếu thực thể trong tiêu đề video
    # để không vứt bỏ oan clip YouTube thật đã tải về chỉ vì Google API tạm quá tải 503!
    try:
        if pre_gate.get("passed", False) and video_title and not is_edit_or_ai_slop_video_metadata(video_title):
            from app.services.media.entity_detector import is_incongruent_stock_image
            if not is_incongruent_stock_image(video_title, topic=topic, narration=narration):
                wm_gate = VisualCritic.inspect_clip_before_concat(
                    video_path,
                    min_short_edge=720,
                    min_sharpness=5.4,
                    check_watermark=True,
                    allow_snow=True,
                    auto_sanitize=False,
                )
                if wm_gate.get("passed", False):
                    anchor_text = f"{subject_entity} {topic} {overall_script_query}".lower()
                    anchor_tokens = {
                        w for w in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", anchor_text)
                        if w not in _YT_QUERY_FILLER_WORDS
                    }
                    title_tokens = set(re.findall(r"[a-zA-ZÀ-ÿ]{4,}", video_title.lower()))
                    if anchor_tokens and len(anchor_tokens.intersection(title_tokens)) >= 1:
                        cv_score = max(7.5, min(8.2, float(wm_gate.get("score", 7.6))))
                        return True, cv_score, f"CV WatermarkGate + Title Anchor verified ({cv_score}/10 while Gemini Vision API 503/rate-limited)"
    except Exception:
        pass

    if strict_no_fallback:
        return False, 0.0, "Strict vision gate: rejected unverified clip"
    return True, 7.2, "Fallback pass"


def _search_youtube_flat_cached(
    search_phrases: List[str],
    subject_anchors: Set[str],
    all_query_tokens: Set[str],
    allow_3d_cgi_monster: bool = False,
    channel_niche: str = "",
) -> List[Dict[str, Any]]:
    """
    Quét song song tối đa 4 nhánh ứng viên YouTube (Kệ Shorts dọc 9:16 + Video gốc 16:9 HD/4K quay thật)
    qua LangGraph `Send` API có bộ nhớ đệm (cache), đồng thời lọc bỏ 0ms ổ video `#edit` so sánh, quiz
    và video AI Veo/Sora/Kling có logo góc.
    """
    import yt_dlp

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 8,
        "ignoreerrors": True,
    }

    def _fetch_single_yt_phrase(sp: str) -> List[Dict[str, Any]]:
        from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx
        if is_scene_cancelled_ctx():
            return []
        cache_key = f"{license_policy.license_mode()}::vdual::{sp.lower().strip()}"
        with _yt_url_lock:
            cached_entries = _yt_flat_search_cache.get(cache_key)
        if cached_entries:
            return cached_entries
        if not _yt_search_sem.acquire(timeout=12.0):
            return []
        try:
            if is_scene_cancelled_ctx():
                return []
            with _yt_url_lock:
                cached_entries = _yt_flat_search_cache.get(cache_key)
            if cached_entries:
                return cached_entries
            # Bước 1: Quét kệ YouTube Shorts dọc 9:16 nguyên bản (`shortsLockupViewModel` / `/shorts/`)
            native_shorts = license_policy.search_youtube_native_shorts_flat(sp, max_results=12)
            seen_s_ids = {str(x.get("id") or "") for x in native_shorts if x.get("id")}
            combined_entries: List[Dict[str, Any]] = list(native_shorts)

            # Bước 2: Chỉ gọi yt_dlp ytsearch6 khi kệ Shorts dọc có ít hơn 2 kết quả
            if len(native_shorts) < 2 and not is_scene_cancelled_ctx():
                yt_query = license_policy.youtube_search_target(sp, max_results=6)
                try:
                    with yt_dlp.YoutubeDL(license_policy.youtube_search_opts(flat_opts, max_results=6)) as ydl:
                        res = ydl.extract_info(yt_query, download=False)
                    for e in ((res or {}).get("entries") or []):
                        if e and isinstance(e, dict):
                            eid = str(e.get("id") or "")
                            if eid and eid not in seen_s_ids:
                                seen_s_ids.add(eid)
                                e["is_vertical_short"] = license_policy.is_entry_vertical_short(e)
                                combined_entries.append(e)
                except Exception:
                    pass
            cached_entries = combined_entries
            if cached_entries:
                with _yt_url_lock:
                    _yt_flat_search_cache[cache_key] = cached_entries
            return cached_entries
        finally:
            _yt_search_sem.release()

    try:
        from app.graph.parallel_search import run_langgraph_parallel_youtube_flat_search
        parallel_buckets = run_langgraph_parallel_youtube_flat_search(
            search_phrases=search_phrases[:4],
            fetch_single_phrase_fn=_fetch_single_yt_phrase,
        )
    except Exception:
        parallel_buckets = [{"items": _fetch_single_yt_phrase(sp)} for sp in search_phrases[:4]]

    candidates: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for bucket in parallel_buckets:
        for entry in (bucket.get("items") or []):
            vid_id = entry.get("id") or ""
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            is_vert_short = license_policy.is_entry_vertical_short(entry)
            webpage_url = (
                f"https://www.youtube.com/shorts/{vid_id}"
                if is_vert_short
                else (entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}")
            )
            if webpage_url.startswith("/"):
                webpage_url = f"https://www.youtube.com{webpage_url}"

            dur = float(entry.get("duration") or (25.0 if is_vert_short else 0.0))
            if dur <= 0 or dur < 4 or dur > 180:
                continue

            title = str(entry.get("title") or "")
            uploader = str(entry.get("uploader") or entry.get("channel") or "")
            if license_policy._VN_EXCLUSIVE_DIACRITIC_RE.search(f"{title} {uploader}"):
                continue
            meta_lower = f"{title} {uploader}".lower()
            if any(bad in meta_lower for bad in _TALKING_HEAD_BLOCKLIST):
                continue
            if any(bad_ch in meta_lower for bad_ch in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST):
                continue
            if is_edit_or_ai_slop_video_metadata(
                title,
                uploader,
                allow_3d_cgi_monster=allow_3d_cgi_monster,
                channel_niche=channel_niche,
            ):
                continue

            title_lower = title.lower()
            anchor_hits = sum(1 for a in subject_anchors if a in title_lower)
            action_hits = sum(1 for tok in all_query_tokens if tok in title_lower)
            views = int(entry.get("view_count") or 0)
            viral_boost = (
                45.0 if views >= 10_000_000 else
                32.0 if views >= 1_000_000 else
                20.0 if views >= 250_000 else
                10.0 if views >= 50_000 else 0.0
            )
            short_fmt_bonus = 25.0 if (0 < dur <= 60) else (15.0 if dur <= 180 else 0.0)
            vertical_native_bonus = 95.0 if is_vert_short else 0.0
            clean_title_bonus = 25.0 if title.count("#") <= 2 else (-20.0 if title.count("#") >= 5 else 0.0)
            score = (
                (anchor_hits * 65.0)
                + (action_hits * 42.0)
                + min(views / 25000.0, 45.0)
                + viral_boost
                + short_fmt_bonus
                + vertical_native_bonus
                + clean_title_bonus
            )
            candidates.append({
                "id": vid_id,
                "url": webpage_url,
                "title": title,
                "uploader": uploader,
                "duration": dur,
                "view_count": views,
                "is_vertical_short": is_vert_short,
                "score": score,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def _rank_youtube_candidates_with_llm(
    candidates: List[Dict[str, Any]],
    topic: str,
    subject_entity: str,
    narration: str,
    search_query: str,
    clip_role: str = "PEAK",
    allow_3d_cgi_monster: bool = False,
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Bộ não LLM Batch Candidate Selector cho YouTube:
    - Gửi danh sách tối đa 20 ứng viên YouTube (`id`, `title`, `uploader`, `duration`, `view_count`, `format`) trong 1 lần gọi JSON duy nhất.
    - Ưu tiên số 1 cho CẢNH QUAY THẬT NGUYÊN BẢN SẠCH CHỮ (cả `9:16_NATIVE_SHORT` lẫn `16:9_LANDSCAPE` tư liệu gốc);
      loại bỏ ngay các video `#edit` so sánh, `"Who would win"` hoặc AI tạo sinh giả cảnh thật.
    """
    if not candidates or len(candidates) <= 1:
        return candidates
    if (cancel_event is not None and cancel_event.is_set()) or (
        deadline_ts is not None and (deadline_ts - time.monotonic()) <= 8.0
    ):
        return candidates

    pool = candidates[:20]
    remainder = candidates[20:]
    items_for_llm = [
        {
            "id": str(c.get("id") or ""),
            "title": str(c.get("title") or "")[:110],
            "channel": str(c.get("uploader") or "")[:45],
            "duration_s": round(float(c.get("duration") or 0.0), 1),
            "views": int(c.get("view_count") or 0),
            "format": "9:16_NATIVE_SHORT" if c.get("is_vertical_short") else "16:9_LANDSCAPE",
        }
        for c in pool
    ]
    mode_desc = (
        "Photorealistic 3D CGI / Unreal Engine 5 / Megalophobia / Thalassophobia scale animation (REJECT 2D cartoons, Minecraft, Roblox, GMod, Lego, tier lists, reactions)"
        if allow_3d_cgi_monster
        else "Real-world high-impact documentary / authentic camera footage (REJECT comparison #edit videos, 'Who would win' debates, AI-generated Veo/Sora/Kling videos, cartoons, video games, slideshows, sleeping/passive pet videos, talking-head explainers, or wrong species/subjects)"
    )

    prompt = f"""You are the Lead LLM Video Candidate Curator for a viral YouTube Shorts studio.
Topic: "{topic}"
Primary Subject: "{subject_entity or topic}"
Scene Role: "{clip_role}"
Scene Narration: "{narration}"
Target Search Query: "{search_query}"
Required Visual Mode: {mode_desc}

Candidate YouTube Videos:
{json.dumps(items_for_llm, ensure_ascii=False)}

TASK:
1. CROSS-SCENE COREFERENCE & CANONICAL ENTITY LOCK:
   - First, resolve any pronouns ("It", "They") or generic role epithets ("the predator", "the hunter", "its living prey") in `Scene Narration` back to the exact canonical species/entities in `Topic` ("{topic}") and `Primary Subject` ("{subject_entity or topic}").
   - Semantically evaluate each candidate title + channel + duration + views + format against the Topic, Primary Subject, and Scene Narration (understand synonyms, scientific names, and contextual meaning—do NOT rely on naive exact word matching, and NEVER approve a different species just because it shares a generic class word like "eagle" or "bird").
2. CLEAN RAW FOOTAGE OVER EDITED SHORTS (CRITICAL):
   - Prioritize CLEAN, AUTHENTIC, UNEDITED RAW FOOTAGE (both `"9:16_NATIVE_SHORT"` and `"16:9_LANDSCAPE"` wildlife/field/documentary/archival recordings—our FFmpeg engine smart-crops 16:9 cleanly!) OVER heavily-edited `#shorts` reposts, `"A vs B"` comparison edits, `"Who would win"` quizzes, or AI-generated Veo clips!
   - DUAL-TRACK "A vs B" RULE: If a candidate is a GENUINE real-life camera encounter of both entities interacting, score it highest (`9.0–10.0`). If `"A vs B"` candidates look like comparison edits, fan montages, debates, or AI renders, REJECT them into `"rejected_ids"` immediately and approve the cleanest single-entity real footage!
   - When two candidates are equally clean & authentic raw footage, give a slight preference (+0.3) to `"9:16_NATIVE_SHORT"`.
3. ZERO TANGENTIAL / SIDE-PROP / TALKING-HEAD TOLERANCE:
   - REJECT any candidate that shows only a tangential side-prop, random room/car/scenery without the Primary Subject, wrong species/subject, comparison `#edit` / quiz / AI-slop clip, passive/boring clip (sleeping pet, static slideshow, 1-hour loop), or talking-head commentary/podcast/reaction/gaming/tier-list video.
4. Score the approved candidates from 0.0 to 10.0 based on authenticity, clear visibility of the Primary Subject in motion, absence of burned-in edit overlays, and dramatic visual impact for this scene.

Return ONLY valid JSON in this exact format:
{{
  "approved": [
    {{"id": "video_id", "llm_score": 9.4}}
  ],
  "rejected_ids": ["bad_video_id_1"]
}}"""

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=3):
                if (cancel_event is not None and cancel_event.is_set()) or (
                    deadline_ts is not None and (deadline_ts - time.monotonic()) <= 6.0
                ):
                    break
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                gen_cfg = KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1)
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg,
                }
                t_req0 = time.monotonic()
                try:
                    resp = requests.post(url, json=payload, timeout=KeyRotator.fast_timeout(10))
                    if resp.status_code == 200:
                        rotator.record_model_success(api_key, model_name, time.monotonic() - t_req0)
                        cands_resp = resp.json().get("candidates") or []
                        if cands_resp:
                            parts_list = cands_resp[0].get("content", {}).get("parts", [])
                            txt_out = "".join(
                                str(p.get("text") or "")
                                for p in parts_list
                                if isinstance(p, dict) and not p.get("thought")
                            ).strip()
                            if not txt_out and parts_list:
                                txt_out = str(parts_list[-1].get("text") or "").strip()
                            m = re.search(r"\{.*\}", txt_out, re.DOTALL)
                            if m:
                                data = json.loads(m.group(0))
                                approved_map = {
                                    str(item.get("id") or ""): float(item.get("llm_score") or 0.0)
                                    for item in (data.get("approved") or [])
                                    if isinstance(item, dict) and item.get("id")
                                }
                                rejected_set = {
                                    str(rid) for rid in (data.get("rejected_ids") or []) if rid
                                }
                                if approved_map or rejected_set:
                                    curated_pool: List[Dict[str, Any]] = []
                                    for c in pool:
                                        cid = str(c.get("id") or "")
                                        if cid in rejected_set and cid not in approved_map:
                                            continue
                                        c_copy = dict(c)
                                        if cid in approved_map:
                                            llm_s = approved_map[cid]
                                            if llm_s < 4.5:
                                                continue
                                            c_copy["llm_score"] = llm_s
                                            c_copy["score"] = float(c_copy.get("score") or 0.0) + (llm_s * 65.0)
                                        curated_pool.append(c_copy)
                                    if curated_pool:
                                        curated_pool.sort(
                                            key=lambda x: (
                                                2 if float(x.get("llm_score") or 0.0) >= 8.8 else (
                                                    1 if float(x.get("llm_score") or 0.0) >= 7.5 else 0
                                                ),
                                                float(x.get("score") or 0.0),
                                            ),
                                            reverse=True,
                                        )
                                        return curated_pool + remainder
                    elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as rank_ex:
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(rank_ex)[:160])
                    continue
    except Exception as ex:
        logger.debug(f"[LLM YouTube Candidate Selector] Fallback pass: {ex}")

    return candidates
