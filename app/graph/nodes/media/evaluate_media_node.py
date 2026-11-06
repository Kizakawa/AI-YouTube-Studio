"""app.graph.nodes.media.evaluate_media_node — Node thẩm định chất lượng kỹ thuật và thị giác của Media."""
import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, List

from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.state import VideoWorkflowState, VisualEvaluationReport, SceneVisualScore

logger = logging.getLogger(__name__)


def node_evaluate_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node Visual Critic thẩm định chất lượng KỸ THUẬT & THỊ GIÁC (Single Responsibility — chỉ chấm điểm và điều hướng sang refine_media, tuyệt đối không tự tải đè media)."""
    scenes_media = [m for m in state.get("scenes_media", []) if isinstance(m, dict)]
    script_d = state.get("script_data") if isinstance(state.get("script_data"), dict) else {}
    raw_topic = str(state.get("topic") or script_d.get("title") or "").strip()
    raw_primary = str(state.get("primary_entity") or script_d.get("primary_entity") or "").strip()
    if raw_topic and raw_primary and raw_primary.lower() not in raw_topic.lower():
        primary_entity = f"{raw_topic} (Primary Entity: {raw_primary})"
    else:
        primary_entity = raw_topic or raw_primary

    critic = VisualCritic()
    retry_count = int(state.get("media_retry_count", 0))
    max_media_retries_gate = max(0, int(os.getenv("MAX_MEDIA_RETRIES", "3")))

    # Luôn đánh giá toàn bộ scenes_media trên cùng 1 Contact Sheet (các cảnh không đổi đã có cache CV 0.01s)
    # để bảo toàn đối chiếu hình thái thực thể xuyên cảnh (Cross-Tile Identity) và chống trùng dHash xuyên cảnh ở mọi vòng retry!
    eval_report = critic.evaluate_media(
        scenes_media=scenes_media,
        primary_entity=primary_entity,
        channel_style=str(state.get("channel_style") or ""),
    )

    # Thu hồi tức thì chứng chỉ (`*_meta.txt` / `*.meta`) của mọi Chapter Master bị VisualCritic ContactSheet Vision bắt lỗi ngữ nghĩa,
    # đảm bảo `node_refine_media` không bao giờ cắt lại từ Master lỗi trên bất kỳ cảnh nào!
    try:
        from app.services.media.longform_chapter_pool import get_chapter_master_meta_paths
        ch_pool_eval = Path(state.get("work_dir", "storage/cache")) / "media" / "chapter_pool"
        if ch_pool_eval.is_dir() and eval_report.failed_scene_ids:
            sc_map_eval = {int(m.get("scene_id") or 0): m for m in scenes_media}
            for sc_score_obj in (eval_report.scene_scores or []):
                sid_ev = int(getattr(sc_score_obj, "scene_id", 0) or 0)
                iss_ev = [str(x) for x in (getattr(sc_score_obj, "issues", None) or [])]
                if sid_ev and any("Gemini Vision ContactSheet" in x for x in iss_ev):
                    m_ev = sc_map_eval.get(sid_ev) or {}
                    blob_ev = f"{m_ev.get('source_url') or ''} {m_ev.get('strategy_rationale') or ''}".lower()
                    for m_name in set(re.findall(r"chapter_\d+_master\.mp4", blob_ev)):
                        m_file_ev = ch_pool_eval / m_name
                        revoked_any_meta = False
                        for m_meta_ev in get_chapter_master_meta_paths(m_file_ev):
                            if m_meta_ev.exists():
                                m_meta_ev.unlink(missing_ok=True)
                                revoked_any_meta = True
                        if revoked_any_meta:
                            logger.warning(
                                f"🛡️ [MasterQuarantine] Thu hồi chứng chỉ '{m_name}' do Cảnh #{sid_ev} bị ContactSheet Vision bắt lỗi ngữ nghĩa!"
                            )
                        try:
                            m_file_ev.unlink(missing_ok=True)
                        except OSError:
                            pass
    except Exception as _e_mq:
        logger.debug(f"[MasterQuarantine] Skip: {_e_mq}")

    # Chặn cứng tuyệt đối mọi phân cảnh còn sót `_graphic.png` hoặc thiếu file trên ổ đĩa
    for sc_chk in scenes_media:
        sid_chk = int(sc_chk.get("scene_id") or 0)
        mp_chk = str(sc_chk.get("media_path") or "")
        if sid_chk > 0 and (
            not mp_chk
            or mp_chk.lower().endswith("_graphic.png")
            or str(sc_chk.get("production_method") or "").upper() == "EMERGENCY_GRAPHIC_CARD"
            or not Path(mp_chk).exists()
        ):
            if sid_chk not in eval_report.failed_scene_ids:
                eval_report.failed_scene_ids.append(sid_chk)
            eval_report.passed = False

    # Cổng kiểm toán tỷ lệ Video chuyển động thật (>= 80% trên Shorts) và 100% 5-7s đầu (Scene 1 & Scene 2) ở mọi vòng đánh giá:
    is_lf_check = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or len(scenes_media) >= 14
    )
    if not is_lf_check and len(scenes_media) >= 5 and retry_count <= max_media_retries_gate:
        from app.utils.ffmpeg_helper import verify_hook_clip_genuine_motion

        def _is_eval_real_video(sc_d: Dict[str, Any]) -> bool:
            raw_mp = str(sc_d.get("media_path") or "")
            mp_s = raw_mp.lower()
            mt_s = str(sc_d.get("media_type") or "").lower()
            pm_s = str(sc_d.get("production_method") or "").upper()
            if any(t_k in mp_s for t_k in ("_i2v", "_fb_motion", "_anim", "_motion3d", "_gsearch_3d", "_creature_3d")):
                return False
            if any(
                t_p in pm_s
                for t_p in (
                    "IMAGE_TO_VIDEO",
                    "STOCK_PHOTO_I2V",
                    "GOOGLE_TOP_IMAGE",
                    "CLIMAX_IMAGES_LIVE_FX",
                    "GOOGLE_MULTI_CHUNK_HOOK_VIDEO",
                    "FINAL_GATE_GOOGLE_IMAGE_RESCUE",
                    "REFINED_GOOGLE_IMAGE_RESCUE",
                )
            ):
                return False
            if not (mt_s in ("video", "hook_ready_video") and mp_s.endswith((".mp4", ".mov", ".webm"))):
                return False
            if int(sc_d.get("scene_id") or 0) == 1:
                ok_gen_m, _, _ = verify_hook_clip_genuine_motion(raw_mp)
                if not ok_gen_m:
                    return False
            return True

        n_sc_eval = len(scenes_media)
        min_req_vid = max(5, int((n_sc_eval * 0.85) + 0.999))
        real_vid_count = sum(1 for sc_x in scenes_media if _is_eval_real_video(sc_x))

        scenes_audio_eval = state.get("scenes_audio") or []
        audio_dur_map_eval: Dict[int, float] = {}
        for a_it in scenes_audio_eval:
            if isinstance(a_it, dict) and a_it.get("scene_id"):
                audio_dur_map_eval[int(a_it["scene_id"])] = max(1.2, float(a_it.get("audio_duration_ms") or 4000.0) / 1000.0)

        def _get_eval_scene_dur(sc_d: Dict[str, Any], idx_fallback: int) -> float:
            sid_d = int(sc_d.get("scene_id") or (idx_fallback + 1))
            if sid_d in audio_dur_map_eval:
                return audio_dur_map_eval[sid_d]
            return max(1.2, float(sc_d.get("duration_sec") or sc_d.get("estimated_seconds") or 4.5))

        total_eval_dur_s = sum(_get_eval_scene_dur(sc_x, i_x) for i_x, sc_x in enumerate(scenes_media)) or 1.0
        real_vid_dur_s = sum(
            _get_eval_scene_dur(sc_x, i_x)
            for i_x, sc_x in enumerate(scenes_media)
            if _is_eval_real_video(sc_x)
        )
        real_vid_dur_ratio = real_vid_dur_s / total_eval_dur_s

        # Bắt buộc 100% 5-7s đầu tiên (Scene 1 & Scene 2) phải là Video chuyển động thật
        opening_failed_sids: List[int] = []
        for open_idx in (0, 1):
            if open_idx < n_sc_eval and not _is_eval_real_video(scenes_media[open_idx]):
                open_sid = int(scenes_media[open_idx].get("scene_id") or (open_idx + 1))
                if open_sid not in eval_report.failed_scene_ids:
                    eval_report.failed_scene_ids.append(open_sid)
                opening_failed_sids.append(open_sid)
        if opening_failed_sids:
            eval_report.passed = False
            logger.warning(
                f"🎬 [Opening 5-7s Pure Video Gate] Cảnh mở đầu {opening_failed_sids} chưa phải là Video chuyển động thật 100% "
                f"-> Đưa bắt buộc vào luồng nâng cấp Video Hook tại refine_media!"
            )

        if real_vid_count < min_req_vid or real_vid_dur_ratio <= 0.85:
            deficit_v = max(1, min_req_vid - real_vid_count)
            # Ưu tiên nâng cấp các cảnh mở đầu, kết thúc và cảnh ảnh tĩnh có thời lượng dài nhất để vượt sàn > 85% thời lượng!
            mid_indices = sorted(
                range(2, max(2, n_sc_eval - 2)),
                key=lambda i_m: _get_eval_scene_dur(scenes_media[i_m], i_m),
                reverse=True,
            )
            static_priority_order = [0, 1, n_sc_eval - 1, n_sc_eval - 2] + mid_indices
            added_static_sids: List[int] = []
            sim_vid_dur_s = real_vid_dur_s
            for st_idx in static_priority_order:
                if 0 <= st_idx < n_sc_eval and not _is_eval_real_video(scenes_media[st_idx]):
                    st_sid = int(scenes_media[st_idx].get("scene_id") or (st_idx + 1))
                    if st_sid not in eval_report.failed_scene_ids:
                        eval_report.failed_scene_ids.append(st_sid)
                        added_static_sids.append(st_sid)
                    sim_vid_dur_s += _get_eval_scene_dur(scenes_media[st_idx], st_idx)
                    if len(added_static_sids) >= deficit_v and (sim_vid_dur_s / total_eval_dur_s) > 0.855:
                        break
            if eval_report.failed_scene_ids:
                eval_report.passed = False
                logger.warning(
                    f"🎬 [MotionFloorGate >85%] Hiện có {real_vid_count}/{n_sc_eval} cảnh video thật "
                    f"({real_vid_dur_ratio * 100:.1f}% thời lượng <= 85.0% chuẩn tối thiểu {min_req_vid} cảnh) "
                    f"-> Đưa các cảnh ảnh tĩnh {added_static_sids} vào refine_media bắt buộc nâng cấp lên Video!"
                )

    logger.info(
        f"🖼️ [VisualCritic] Điểm kỹ thuật media: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Lỗi: {len(eval_report.failed_scene_ids)} cảnh"
    )

    return {
        "scenes_media": scenes_media,
        "media_eval": eval_report.model_dump(),
        "current_step": "MEDIA_EVALUATED",
        "progress_percent": 70,
        "progress_message": f"Thẩm định kỹ thuật hình ảnh: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else f'Cần sửa {len(eval_report.failed_scene_ids)} cảnh'})",
    }
