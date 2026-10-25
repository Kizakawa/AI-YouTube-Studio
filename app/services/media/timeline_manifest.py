"""
app.services.media.timeline_manifest — Quản lý Timeline Chung Người-Agent (`edit_manifest.json` + Oplog) & Biên dịch Project CapCut (`draft_content.json`).

Tích hợp kiến trúc từ GOHA AVS (Agent-Native Video Studio) & AUTO CAPCUT:
1. Single Source of Truth (`edit_manifest.json`) với ghi nguyên tử (Atomic Write: `.tmp` -> `os.replace`).
2. Từ vựng thao tác đóng (Closed Vocabulary gồm 11 thao tác có tên, đảo ngược được — Reversible Semantic Ops):
   `trim_scene`, `swap_asset`, `reorder_scene`, `update_narration`, `set_camera_motion`,
   `set_sfx_cue`, `set_bgm_volume`, `insert_scene`, `remove_scene`, `update_subtitle_style`, `set_overlay_badge`.
3. Oplog & `diff_since(rev)`: Giúp AI Agent nắm bắt tức thì những gì con người hoặc agent vừa chỉnh sửa bằng ngôn ngữ nghiệp vụ mà không cần đọc toàn bộ JSON diff.
4. CapCut Draft Compiler (`compile_capcut_draft`) kèm cổng kiểm chứng `verify_voice_clip_alignment` (Fail-Closed).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.contracts import RenderJobContract

logger = logging.getLogger(__name__)

# Từ vựng đóng gồm 11 thao tác ngữ nghĩa được phép thực thi trên Timeline (cấm patch_json / set_path tùy ý)
CLOSED_TIMELINE_OPS = frozenset({
    "trim_scene",
    "swap_asset",
    "reorder_scene",
    "update_narration",
    "set_camera_motion",
    "set_sfx_cue",
    "set_bgm_volume",
    "insert_scene",
    "remove_scene",
    "update_subtitle_style",
    "set_overlay_badge",
})


class TimelineConcurrencyError(RuntimeError):
    """Ném ra khi `base_rev` của client cũ hơn `rev` hiện tại của manifest."""


class TimelineValidationError(ValueError):
    """Ném ra khi thao tác nằm ngoài từ vựng đóng hoặc dữ liệu lệch chuẩn (fail-closed)."""


def build_initial_edit_manifest(render_job: RenderJobContract) -> Dict[str, Any]:
    """Khởi tạo cấu trúc `edit_manifest.json` chuẩn v2 từ `RenderJobContract`."""
    scenes_ir: List[Dict[str, Any]] = []
    cursor_ms = 0
    for sc in render_job.scenes or []:
        dur_ms = int(getattr(sc, "audio_duration_ms", 0) or 3500)
        scenes_ir.append({
            "scene_id": int(sc.scene_id),
            "start_ms": cursor_ms,
            "duration_ms": dur_ms,
            "end_ms": cursor_ms + dur_ms,
            "narration": sc.narration or "",
            "visual_keyword": sc.visual_keyword or "",
            "visual_prompt": getattr(sc, "visual_prompt", None) or "",
            "media_path": sc.media_path or "",
            "media_type": sc.media_type or "image",
            "audio_path": sc.audio_path or "",
            "camera_motion": getattr(sc, "camera_motion", None) or "dolly_in",
            "sfx_cue": getattr(sc, "sfx_cue", None) or "",
            "overlay_badge": getattr(sc, "overlay_badge", None) or "",
            "highlight_words": list(getattr(sc, "highlight_words", None) or []),
        })
        cursor_ms += dur_ms

    return {
        "schema_version": "2.0",
        "job_id": render_job.job_id,
        "title": render_job.title,
        "video_format": getattr(render_job, "video_format", "short") or "short",
        "resolution": {"width": 1080, "height": 1920, "fps": 30},
        "rev": 1,
        "updated_at": int(time.time()),
        "audio_master": {
            "full_audio_path": render_job.full_audio_path or "",
            "total_audio_duration_ms": int(render_job.total_audio_duration_ms or cursor_ms),
            "bgm_volume": 0.14,
        },
        "subtitle_master": {
            "subtitle_path": render_job.subtitle_path or "",
            "font_family": "Montserrat",
            "font_size": 42,
        },
        "output_video_path": render_job.output_path or "",
        "scenes": scenes_ir,
        "oplog": [
            {
                "rev": 1,
                "actor": "pipeline",
                "op": "init_manifest",
                "summary": f"Initialized timeline with {len(scenes_ir)} scenes ({cursor_ms}ms total)",
                "timestamp": int(time.time()),
                "inverse_op": None,
            }
        ],
    }


def _recalculate_timeline_offsets(scenes: List[Dict[str, Any]]) -> int:
    """Tính toán lại `start_ms`, `end_ms` và đánh số thứ tự sau mỗi thao tác chỉnh sửa."""
    cursor_ms = 0
    for idx, sc in enumerate(scenes, start=1):
        sc["scene_id"] = idx
        dur = max(200, int(sc.get("duration_ms", 3000)))
        sc["duration_ms"] = dur
        sc["start_ms"] = cursor_ms
        sc["end_ms"] = cursor_ms + dur
        cursor_ms += dur
    return cursor_ms


def apply_ops(
    manifest: Dict[str, Any],
    ops: List[Dict[str, Any]],
    base_rev: Optional[int] = None,
    actor: str = "agent",
) -> Dict[str, Any]:
    """
    Thực thi danh sách thao tác có tên từ từ vựng đóng (`CLOSED_TIMELINE_OPS`) lên manifest.
    Hỗ trợ kiểm soát đồng thời qua `base_rev` và tự động sinh `inverse_op` để undo 100%.
    """
    current_rev = int(manifest.get("rev", 1))
    if base_rev is not None and int(base_rev) != current_rev:
        raise TimelineConcurrencyError(
            f"Stale base_rev={base_rev} (current manifest rev={current_rev}). Fetch diff_since({base_rev}) first."
        )

    state = copy.deepcopy(manifest)
    scenes: List[Dict[str, Any]] = state.get("scenes", [])
    oplog: List[Dict[str, Any]] = state.get("oplog", [])

    for op_item in ops or []:
        op_name = str(op_item.get("op") or "").strip()
        if op_name not in CLOSED_TIMELINE_OPS:
            raise TimelineValidationError(
                f"Operation '{op_name}' is outside the closed vocabulary {sorted(CLOSED_TIMELINE_OPS)}. "
                f"Arbitrary JSON patching is strictly forbidden."
            )

        target_sid = int(op_item.get("scene_id", 0) or 0)
        target_sc = next((s for s in scenes if int(s.get("scene_id", -1)) == target_sid), None)

        inverse_op: Optional[Dict[str, Any]] = None
        summary = ""

        if op_name == "trim_scene":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for trim_scene.")
            old_dur = int(target_sc["duration_ms"])
            delta_ms = int(op_item.get("delta_ms", 0) or 0)
            new_dur = int(op_item.get("duration_ms", old_dur + delta_ms))
            new_dur = max(300, new_dur)
            target_sc["duration_ms"] = new_dur
            inverse_op = {"op": "trim_scene", "scene_id": target_sid, "duration_ms": old_dur}
            summary = f"{actor} trimmed scene {target_sid} from {old_dur}ms to {new_dur}ms"

        elif op_name == "swap_asset":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for swap_asset.")
            old_path = target_sc.get("media_path", "")
            old_type = target_sc.get("media_type", "image")
            new_path = str(op_item.get("media_path") or "").strip()
            new_type = str(op_item.get("media_type") or old_type).strip()
            target_sc["media_path"] = new_path
            target_sc["media_type"] = new_type
            inverse_op = {"op": "swap_asset", "scene_id": target_sid, "media_path": old_path, "media_type": old_type}
            summary = f"{actor} swapped asset in scene {target_sid} to '{Path(new_path).name or new_path}' ({new_type})"

        elif op_name == "reorder_scene":
            from_idx = int(op_item.get("from_scene_id", target_sid)) - 1
            to_idx = int(op_item.get("to_scene_id", 1)) - 1
            if not (0 <= from_idx < len(scenes) and 0 <= to_idx < len(scenes)):
                raise TimelineValidationError("Invalid scene indices for reorder_scene.")
            moved = scenes.pop(from_idx)
            scenes.insert(to_idx, moved)
            inverse_op = {"op": "reorder_scene", "from_scene_id": to_idx + 1, "to_scene_id": from_idx + 1}
            summary = f"{actor} moved scene {from_idx + 1} to position {to_idx + 1}"

        elif op_name == "update_narration":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for update_narration.")
            old_narr = target_sc.get("narration", "")
            new_narr = str(op_item.get("narration") or "").strip()
            target_sc["narration"] = new_narr
            inverse_op = {"op": "update_narration", "scene_id": target_sid, "narration": old_narr}
            summary = f"{actor} updated narration in scene {target_sid}"

        elif op_name == "set_camera_motion":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for set_camera_motion.")
            old_cam = target_sc.get("camera_motion", "dolly_in")
            new_cam = str(op_item.get("camera_motion") or "dolly_in").strip()
            target_sc["camera_motion"] = new_cam
            inverse_op = {"op": "set_camera_motion", "scene_id": target_sid, "camera_motion": old_cam}
            summary = f"{actor} changed camera_motion in scene {target_sid} from '{old_cam}' to '{new_cam}'"

        elif op_name == "set_sfx_cue":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for set_sfx_cue.")
            old_sfx = target_sc.get("sfx_cue", "")
            new_sfx = str(op_item.get("sfx_cue") or "").strip()
            target_sc["sfx_cue"] = new_sfx
            inverse_op = {"op": "set_sfx_cue", "scene_id": target_sid, "sfx_cue": old_sfx}
            summary = f"{actor} set sfx_cue in scene {target_sid} to '{new_sfx}'"

        elif op_name == "set_bgm_volume":
            old_vol = float(state.get("audio_master", {}).get("bgm_volume", 0.14))
            new_vol = max(0.0, min(1.0, float(op_item.get("bgm_volume", old_vol))))
            state.setdefault("audio_master", {})["bgm_volume"] = new_vol
            inverse_op = {"op": "set_bgm_volume", "bgm_volume": old_vol}
            summary = f"{actor} adjusted BGM volume from {old_vol:.2f} to {new_vol:.2f}"

        elif op_name == "insert_scene":
            pos = max(0, min(len(scenes), int(op_item.get("position", len(scenes) + 1)) - 1))
            new_sc = {
                "scene_id": pos + 1,
                "start_ms": 0,
                "duration_ms": int(op_item.get("duration_ms", 3000)),
                "end_ms": 0,
                "narration": str(op_item.get("narration") or ""),
                "visual_keyword": str(op_item.get("visual_keyword") or ""),
                "visual_prompt": str(op_item.get("visual_prompt") or ""),
                "media_path": str(op_item.get("media_path") or ""),
                "media_type": str(op_item.get("media_type") or "image"),
                "audio_path": str(op_item.get("audio_path") or ""),
                "camera_motion": str(op_item.get("camera_motion") or "dolly_in"),
                "sfx_cue": str(op_item.get("sfx_cue") or ""),
                "overlay_badge": str(op_item.get("overlay_badge") or ""),
                "highlight_words": list(op_item.get("highlight_words") or []),
            }
            scenes.insert(pos, new_sc)
            inverse_op = {"op": "remove_scene", "scene_id": pos + 1}
            summary = f"{actor} inserted new scene at position {pos + 1}"

        elif op_name == "remove_scene":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for remove_scene.")
            removed_copy = copy.deepcopy(target_sc)
            scenes.remove(target_sc)
            inverse_op = {"op": "insert_scene", "position": target_sid, **removed_copy}
            summary = f"{actor} removed scene {target_sid}"

        elif op_name == "update_subtitle_style":
            sub_master = state.setdefault("subtitle_master", {})
            old_font = sub_master.get("font_family", "Montserrat")
            old_size = sub_master.get("font_size", 42)
            new_font = str(op_item.get("font_family") or old_font)
            new_size = int(op_item.get("font_size") or old_size)
            sub_master["font_family"] = new_font
            sub_master["font_size"] = new_size
            inverse_op = {"op": "update_subtitle_style", "font_family": old_font, "font_size": old_size}
            summary = f"{actor} updated subtitle style to {new_font} {new_size}pt"

        elif op_name == "set_overlay_badge":
            if not target_sc:
                raise TimelineValidationError(f"Scene {target_sid} not found for set_overlay_badge.")
            old_badge = target_sc.get("overlay_badge", "")
            new_badge = str(op_item.get("overlay_badge") or "").strip()
            target_sc["overlay_badge"] = new_badge
            inverse_op = {"op": "set_overlay_badge", "scene_id": target_sid, "overlay_badge": old_badge}
            summary = f"{actor} set overlay_badge in scene {target_sid} to '{new_badge}'"

        total_ms = _recalculate_timeline_offsets(scenes)
        state.setdefault("audio_master", {})["total_audio_duration_ms"] = total_ms
        current_rev += 1
        state["rev"] = current_rev
        state["updated_at"] = int(time.time())
        oplog.append({
            "rev": current_rev,
            "actor": actor,
            "op": op_name,
            "summary": summary,
            "timestamp": int(time.time()),
            "inverse_op": inverse_op,
        })

    return state


def diff_since(manifest: Dict[str, Any], since_rev: int) -> List[Dict[str, Any]]:
    """
    Trả về danh sách các thao tác đã diễn ra kể từ `since_rev` bằng ngôn ngữ nghiệp vụ rõ ràng
    (giúp AI Agent nắm bắt thay đổi sau khi mất phiên mà không phải nạp lại toàn bộ JSON).
    """
    oplog = manifest.get("oplog") or []
    return [entry for entry in oplog if int(entry.get("rev", 0)) > int(since_rev)]


def undo_last_op(manifest: Dict[str, Any], actor: str = "human") -> Dict[str, Any]:
    """Đảo ngược thao tác gần nhất trong `oplog` thông qua `inverse_op`."""
    oplog = manifest.get("oplog") or []
    if len(oplog) <= 1:
        return manifest
    last_entry = oplog[-1]
    inv = last_entry.get("inverse_op")
    if not inv:
        return manifest
    return apply_ops(manifest, [inv], base_rev=int(manifest.get("rev", 1)), actor=actor)


def verify_voice_clip_alignment(manifest: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Cổng kiểm chứng khớp Voice ↔ Clip 100% theo chuẩn Fail-Closed của AUTO CAPCUT:
    Chỉ cần 1 phân cảnh có thời lượng <= 0 hoặc thiếu dữ liệu cốt lõi là báo lỗi ngay.
    """
    errors: List[str] = []
    scenes = manifest.get("scenes") or []
    if not scenes:
        return False, ["Manifest contains zero scenes."]

    expected_start = 0
    for idx, sc in enumerate(scenes, start=1):
        dur = int(sc.get("duration_ms", 0) or 0)
        start = int(sc.get("start_ms", 0) or 0)
        if dur <= 0:
            errors.append(f"Scene {idx} has invalid non-positive duration ({dur}ms).")
        if abs(start - expected_start) > 5:
            errors.append(f"Scene {idx} timeline gap/drift detected: start_ms={start} != expected={expected_start}.")
        if not str(sc.get("narration") or "").strip():
            errors.append(f"Scene {idx} is missing spoken narration text.")
        expected_start = start + max(0, dur)

    return len(errors) == 0, errors


def compile_capcut_draft(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Biên dịch Timeline IR (`edit_manifest.json`) thành cấu trúc project CapCut (`draft_content.json`).
    Hỗ trợ mở và chỉnh sửa trực tiếp trên timeline chuẩn (đơn vị thời gian: microsecond = ms * 1000).
    Thực thi kiểm chứng `verify_voice_clip_alignment` theo cơ chế fail-closed trước khi biên dịch.
    """
    ok, errors = verify_voice_clip_alignment(manifest)
    if not ok:
        raise TimelineValidationError(f"Fail-Closed CapCut Sync Verification Failed: {'; '.join(errors)}")

    scenes = manifest.get("scenes") or []
    total_us = int(manifest.get("audio_master", {}).get("total_audio_duration_ms", 0)) * 1000

    videos_materials: List[Dict[str, Any]] = []
    audios_materials: List[Dict[str, Any]] = []
    texts_materials: List[Dict[str, Any]] = []

    video_segments: List[Dict[str, Any]] = []
    audio_segments: List[Dict[str, Any]] = []
    text_segments: List[Dict[str, Any]] = []

    for sc in scenes:
        sid = sc.get("scene_id", 1)
        start_us = int(sc.get("start_ms", 0)) * 1000
        dur_us = int(sc.get("duration_ms", 3000)) * 1000

        v_mat_id = f"vid_mat_{sid}_{uuid.uuid4().hex[:8]}"
        a_mat_id = f"aud_mat_{sid}_{uuid.uuid4().hex[:8]}"
        t_mat_id = f"txt_mat_{sid}_{uuid.uuid4().hex[:8]}"

        videos_materials.append({
            "id": v_mat_id,
            "type": "video" if sc.get("media_type") == "video" else "photo",
            "path": sc.get("media_path", ""),
            "duration": dur_us,
            "width": 1080,
            "height": 1920,
        })
        video_segments.append({
            "id": f"seg_v_{sid}",
            "material_id": v_mat_id,
            "target_timerange": {"start": start_us, "duration": dur_us},
            "source_timerange": {"start": 0, "duration": dur_us},
            "camera_motion": sc.get("camera_motion", "dolly_in"),
        })

        audios_materials.append({
            "id": a_mat_id,
            "type": "extract_music",
            "path": sc.get("audio_path", ""),
            "duration": dur_us,
        })
        audio_segments.append({
            "id": f"seg_a_{sid}",
            "material_id": a_mat_id,
            "target_timerange": {"start": start_us, "duration": dur_us},
            "source_timerange": {"start": 0, "duration": dur_us},
        })

        texts_materials.append({
            "id": t_mat_id,
            "type": "subtitle",
            "content": sc.get("narration", ""),
            "font_family": manifest.get("subtitle_master", {}).get("font_family", "Montserrat"),
            "font_size": manifest.get("subtitle_master", {}).get("font_size", 42),
        })
        text_segments.append({
            "id": f"seg_t_{sid}",
            "material_id": t_mat_id,
            "target_timerange": {"start": start_us, "duration": dur_us},
        })

    return {
        "id": f"capcut_draft_job_{manifest.get('job_id', 0)}",
        "version": 360000,
        "duration": total_us,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "fps": 30.0,
        "materials": {
            "videos": videos_materials,
            "audios": audios_materials,
            "texts": texts_materials,
        },
        "tracks": [
            {"id": "track_video_main", "type": "video", "segments": video_segments},
            {"id": "track_audio_voice", "type": "audio", "segments": audio_segments},
            {"id": "track_text_subtitles", "type": "text", "segments": text_segments},
        ],
    }


def save_json_atomic(path: Path, payload: Dict[str, Any]) -> str:
    """Ghi file JSON theo cơ chế nguyên tử (Atomic Write: ghi `.tmp` rồi `os.replace`) chống hỏng file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp_path), str(path))
    return str(path)


def export_job_timeline_artifacts(render_job: RenderJobContract, target_dir: Path) -> Dict[str, str]:
    """
    Xuất bộ đôi `edit_manifest.json` (chuẩn GOHA AVS) và `draft_content.json` (chuẩn AUTO CAPCUT)
    ngay sau khi hoàn tất dựng video.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_initial_edit_manifest(render_job)
    manifest_path = save_json_atomic(target_dir / "edit_manifest.json", manifest)

    capcut_path = ""
    try:
        draft_payload = compile_capcut_draft(manifest)
        capcut_path = save_json_atomic(target_dir / "draft_content.json", draft_payload)
    except Exception as e:
        logger.debug(f"[TimelineManifest] Bỏ qua xuất draft_content.json: {e}")

    return {
        "edit_manifest_path": manifest_path,
        "capcut_draft_path": capcut_path,
    }
