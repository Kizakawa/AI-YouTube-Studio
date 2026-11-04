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
    primary_entity = state.get("primary_entity")

    critic = VisualCritic()
    prev_eval = state.get("media_eval") if isinstance(state.get("media_eval"), dict) else None
    prev_failed_ids = set(int(x) for x in (prev_eval.get("failed_scene_ids") or []) if x) if prev_eval else set()
    retry_count = int(state.get("media_retry_count", 0))
    max_media_retries_gate = max(0, int(os.getenv("MAX_MEDIA_RETRIES", "1")))

    # Tối ưu tốc độ: Ở vòng retry (media_retry_count >= 1), chỉ chạy thẩm định chuyên sâu (ContactSheet Vision + CV)
    # trên các cảnh vừa được refine_media thay mới, giữ nguyên điểm của các cảnh đã đạt chuẩn ở vòng 1!
    if retry_count >= 1 and prev_eval and prev_failed_ids and len(scenes_media) > len(prev_failed_ids):
        delta_scenes = [m for m in scenes_media if int(m.get("scene_id") or 0) in prev_failed_ids]
        delta_report = critic.evaluate_media(scenes_media=delta_scenes, primary_entity=primary_entity)
        merged_scores_map: Dict[int, SceneVisualScore] = {}
        for old_sc in (prev_eval.get("scene_scores") or []):
            try:
                sc_obj = old_sc if isinstance(old_sc, SceneVisualScore) else SceneVisualScore(**old_sc)
                merged_scores_map[int(sc_obj.scene_id)] = sc_obj
            except Exception:
                pass
        for new_sc in (delta_report.scene_scores or []):
            merged_scores_map[int(new_sc.scene_id)] = new_sc
        merged_scores = [merged_scores_map[k] for k in sorted(merged_scores_map.keys())]

        def _sc_avg(s_item: SceneVisualScore) -> float:
            return (float(s_item.relevance_score) + float(s_item.entity_match_score) + float(s_item.aesthetic_score)) / 3.0

        avg_overall = round(sum(_sc_avg(s) for s in merged_scores) / max(len(merged_scores), 1), 1)
        eval_report = VisualEvaluationReport(
            overall_score=avg_overall,
            passed=(len(delta_report.failed_scene_ids) == 0 and avg_overall >= 7.5),
            scene_scores=merged_scores,
            failed_scene_ids=list(delta_report.failed_scene_ids),
            summary=delta_report.summary,
        )
    else:
        eval_report = critic.evaluate_media(scenes_media=scenes_media, primary_entity=primary_entity)

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

    # Cổng kiểm toán tỷ lệ Video chuyển động thật (>= 80% trên Shorts) và 100% 5-7s đầu (Scene 1 & Scene 2) ở vòng đánh giá đầu tiên:
    is_lf_check = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or len(scenes_media) >= 14
    )
    if not is_lf_check and len(scenes_media) >= 5 and retry_count < max_media_retries_gate:
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
            if int(sc_d.get("scene_id") or 0) in (1, 2):
                ok_gen_m, _, _ = verify_hook_clip_genuine_motion(raw_mp)
                if not ok_gen_m:
                    return False
            return True

        n_sc_eval = len(scenes_media)
        min_req_vid = max(5, int((n_sc_eval * 0.80) + 0.999))
        real_vid_count = sum(1 for sc_x in scenes_media if _is_eval_real_video(sc_x))

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

        if real_vid_count < min_req_vid:
            deficit_v = min_req_vid - real_vid_count
            static_priority_order = [0, 1, n_sc_eval - 1, n_sc_eval - 2] + [
                i_m for i_m in range(2, max(2, n_sc_eval - 2))
            ]
            added_static_sids: List[int] = []
            for st_idx in static_priority_order:
                if 0 <= st_idx < n_sc_eval and not _is_eval_real_video(scenes_media[st_idx]):
                    st_sid = int(scenes_media[st_idx].get("scene_id") or (st_idx + 1))
                    if st_sid not in eval_report.failed_scene_ids:
                        eval_report.failed_scene_ids.append(st_sid)
                        added_static_sids.append(st_sid)
                    if len(added_static_sids) >= deficit_v:
                        break
            if eval_report.failed_scene_ids:
                eval_report.passed = False
                logger.warning(
                    f"🎬 [MotionFloorGate] Chỉ có {real_vid_count}/{n_sc_eval} cảnh là video chuyển động thật (< {min_req_vid} cảnh chuẩn >=80%) "
                    f"-> Đưa thêm các cảnh ảnh tĩnh {added_static_sids} vào refine_media!"
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
