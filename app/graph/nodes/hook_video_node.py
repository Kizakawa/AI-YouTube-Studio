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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from config.settings import settings
from app.graph.state import VideoWorkflowState
from app.services.media import license_policy
from app.utils.langsmith_tracer import trace_custom_span
from app.utils.ffmpeg_helper import (
    trim_video,
    get_duration_ms,
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
    """Tạo danh sách truy vấn dự phòng ngắn gọn (2-5 từ Tiếng Anh chuẩn) bám sát hành động vật lý thực tế của toàn bộ kịch bản."""
    queries: List[str] = []

    def _extract_ascii_words(text: str) -> List[str]:
        # Chỉ giữ lại các từ Tiếng Anh nguyên vẹn (không chứa ký tự dấu Tiếng Việt để tránh vỡ thành 'c s u' khi xóa dấu)
        cleaned: List[str] = []
        for tok in str(text or "").split():
            tok_alpha = re.sub(r"[^a-zA-ZÀ-ỹ]", "", tok)
            if tok_alpha and tok_alpha.isascii() and len(tok_alpha) >= 3:
                cleaned.append(tok_alpha)
        return cleaned

    stop_words = {
        "cinematic", "photorealistic", "dramatic", "lighting", "4k", "8k", "vertical",
        "why", "how", "what", "when", "secret", "behind", "truth", "about", "never",
        "psychology", "manipulation", "narcissist", "gaslighting", "mindset", "stoicism",
        "scene", "video", "shorts", "short",
    }
    kw_tokens = [w for w in _extract_ascii_words(visual_keyword) if w.lower() not in stop_words][:4]
    ent_tokens = [w for w in _extract_ascii_words(primary_entity) if w.lower() not in stop_words][:3]
    top_tokens = [w for w in _extract_ascii_words(topic) if w.lower() not in stop_words][:4]
    if not kw_tokens and full_script_text:
        kw_matches = re.findall(r"\[kw:\s*([^\]]+)\]", full_script_text)
        for kw_m in kw_matches[:2]:
            kw_tokens.extend(w for w in _extract_ascii_words(kw_m) if w.lower() not in stop_words)
        kw_tokens = list(dict.fromkeys(kw_tokens))[:4]

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
            seen_m = set()
            merged_toks = []
            for tok in ent_tokens + kw_tokens[:2]:
                if tok.lower() not in seen_m:
                    seen_m.add(tok.lower())
                    merged_toks.append(tok)
            merged = " ".join(merged_toks)
            queries.append(f"{merged} {action_suffix}".strip())
        if kw_tokens:
            queries.append(" ".join(kw_tokens))
        if top_tokens:
            queries.append(" ".join(top_tokens))
        if full_script_text:
            for kw_m in re.findall(r"\[kw:\s*([^\]]+)\]", full_script_text)[:3]:
                extra_kw = [w for w in _extract_ascii_words(kw_m) if w.lower() not in stop_words][:4]
                if extra_kw:
                    queries.append(" ".join(extra_kw))
        core_tokens = ent_tokens or kw_tokens[:2] or top_tokens[:2]
        if core_tokens:
            queries.append(f"{' '.join(core_tokens)} real caught on camera")

    unique_q = []
    for q in queries:
        q_clean = re.sub(r"\s+", " ", q).strip()
        if q_clean and q_clean.lower() not in [x.lower() for x in unique_q]:
            unique_q.append(q_clean)
    return unique_q[:5] or ["cinematic dramatic motion"]


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
    punch_in_b: bool = False,
) -> bool:
    """
    Dựng Hook Đa Nhịp Cắt Nhanh (Multi-Beat Fast-Cut Hook) ngay trong Cảnh 1:
    - Khóa cứng nhịp cắt #1 tại đúng `1.12s` (không phụ thuộc tổng thời lượng Cảnh 1 dài 3s hay 8s).
    - Khi `total_duration_sec >= 2.65s`, tự động tách thành 3 nhịp thị giác liên hoàn (Beat 1A `0.00s->1.12s`,
      Beat 1B `1.12s->2.24s` đổi góc máy/hflip+punch-in 1.26x, Beat 1C `2.24s->end` cận cảnh 1.22x)
      để đảm bảo 100% video luôn có `cuts_first_3s >= 2` ngay trong 2.3 giây đầu tiên!
    - Đảm bảo tổng thời lượng khớp chính xác 100% với `total_duration_sec` (0ms lệch audio).
    """
    if total_duration_sec < 2.0:
        return False

    use_three_beats = total_duration_sec >= 2.65
    if use_three_beats:
        dur_a = 1.12
        dur_b = 1.12
        dur_c = round(max(0.41, total_duration_sec - dur_a - dur_b), 2)
    else:
        dur_a = round(min(1.20, max(0.95, total_duration_sec * 0.46)), 2)
        dur_b = round(max(0.80, total_duration_sec - dur_a), 2)
        dur_c = 0.0

    beat_a_out = str(work_dir / "hook_beat_1a_tmp.mp4")
    beat_b_out = str(work_dir / "hook_beat_1b_tmp.mp4")
    beat_b_raw = str(work_dir / "hook_beat_1b_raw_tmp.mp4")
    beat_c_out = str(work_dir / "hook_beat_1c_tmp.mp4")
    beat_c_raw = str(work_dir / "hook_beat_1c_raw_tmp.mp4")
    concat_list_path = work_dir / "hook_dual_beat_concat.txt"

    try:
        for _p_clean in (beat_a_out, beat_b_out, beat_b_raw, beat_c_out, beat_c_raw):
            Path(_p_clean).unlink(missing_ok=True)

        # Đảm bảo start_b không vượt quá độ dài file clip_b_path
        clip_b_dur_s = max(0.0, get_duration_ms(clip_b_path) / 1000.0)
        eff_start_b = start_b
        if clip_b_dur_s > 0.0 and (eff_start_b + dur_b + 0.15) > clip_b_dur_s:
            eff_start_b = max(0.0, clip_b_dur_s - dur_b - 0.15)

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
            output_path=beat_b_raw if punch_in_b else beat_b_out,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=niche,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=fair_use_b,
            start_offset_sec=eff_start_b,
            preserve_continuous_flow=True,
        )
        if punch_in_b and Path(beat_b_raw).exists() and Path(beat_b_raw).stat().st_size > 8_000:
            # Punch-In 1.28x lệch tâm 1/3 trên + tăng tương phản (TUYỆT ĐỐI KHÔNG dùng hflip để tránh lật ngược chữ/biển hiệu/số liệu trong cảnh thật)
            # tạo cú đổi góc máy rõ rệt 100% tại giây 1.12s (vượt chắc chắn ngưỡng scdet=t=8.5)
            cmd_punch = [
                "ffmpeg", "-y",
                "-i", beat_b_raw,
                "-vf", "scale=1382:2456,crop=1080:1920:(iw-1080)*0.28:(ih-1920)*0.30,eq=contrast=1.08:brightness=0.02,setsar=1",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", "30", "-an",
                beat_b_out,
            ]
            subprocess.run(cmd_punch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            if not (Path(beat_b_out).exists() and Path(beat_b_out).stat().st_size > 8_000):
                beat_b_out = beat_b_raw

        if not (Path(beat_a_out).exists() and Path(beat_b_out).exists()):
            return False
        if Path(beat_a_out).stat().st_size < 8_000 or Path(beat_b_out).stat().st_size < 8_000:
            return False

        concat_files = [beat_a_out, beat_b_out]

        if use_three_beats and dur_c >= 0.40:
            clip_a_dur_s = max(0.0, get_duration_ms(clip_a_path) / 1000.0)
            eff_start_c = start_a + dur_a
            if clip_a_dur_s > 0.0 and (eff_start_c + dur_c + 0.15) > clip_a_dur_s:
                eff_start_c = max(0.0, clip_a_dur_s - dur_c - 0.15)
            trim_video(
                input_path=clip_a_path,
                duration_seconds=dur_c,
                output_path=beat_c_raw,
                width=1080,
                height=1920,
                fps=30,
                is_scifi=is_scifi,
                niche=niche,
                apply_grading=True,
                is_hook_scene=True,
                fair_use_transform=fair_use_a,
                start_offset_sec=eff_start_c,
                preserve_continuous_flow=True,
            )
            if Path(beat_c_raw).exists() and Path(beat_c_raw).stat().st_size > 6_000:
                cmd_punch_c = [
                    "ffmpeg", "-y",
                    "-i", beat_c_raw,
                    "-vf", "scale=1318:2342,crop=1080:1920:(iw-1080)*0.65:(ih-1920)*0.38,eq=contrast=1.05:saturation=1.06,setsar=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-r", "30", "-an",
                    beat_c_out,
                ]
                subprocess.run(cmd_punch_c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                if Path(beat_c_out).exists() and Path(beat_c_out).stat().st_size > 6_000:
                    concat_files.append(beat_c_out)
                else:
                    concat_files.append(beat_c_raw)

        lines_concat = [
            f"file '{str(Path(fp).resolve()).replace(chr(92), '/')}'"
            for fp in concat_files
        ]
        concat_list_path.write_text("\n".join(lines_concat) + "\n", encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path),
            "-t", f"{total_duration_sec:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-r", "30", "-an",
            output_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        ok = Path(output_path).exists() and Path(output_path).stat().st_size > 15_000
        return ok
    except Exception as e_db:
        logger.debug(f"[HookVideoNode] Dual-beat splice skip: {e_db}")
        return False
    finally:
        for tmp_p in (beat_a_out, beat_b_out, beat_b_raw, beat_c_out, beat_c_raw, str(concat_list_path)):
            try:
                Path(tmp_p).unlink(missing_ok=True)
            except Exception:
                pass


def _ensure_hook_clip_sharpness_and_luma(
    clip_path: str,
    min_sharpness: float = 12.5,
) -> tuple[bool, float]:
    """
    Đo độ sắc nét (`hook_frame_sharpness`) & độ sáng tại 2 mốc mở đầu (`0.25s` và `0.85s`) của clip Hook.
    Nếu độ nét < `min_sharpness` (12.5) hoặc khung hình thiếu tương phản/tối mờ (như Job #557 10.02 hoặc Job #559 9.27):
    - Tầng 1: Chạy bộ lọc FFmpeg nâng sáng vùng tối + tăng nét vi mô (`eq` + Multi-Scale `unsharp`).
    - Tầng 2 (Nếu Tầng 1 vẫn < min_sharpness): Nâng tương phản cục bộ + tăng cường biên cạnh 3 lớp (`unsharp` + `cas`)
      để đảm bảo `hook_frame_sharpness >= 12.5` vượt qua cổng `QualityEvaluator` (12.0).
    """
    import shutil
    import tempfile
    from app.graph.evaluators.visual_critic import VisualCritic

    p_clip = Path(clip_path)
    if not p_clip.exists() or p_clip.stat().st_size < 8_000:
        return False, 0.0

    def _probe_clip_sharpness(target_mp4: str) -> tuple[bool, float]:
        probe_times = (0.25, 0.35, 0.85, 1.00)
        scores: List[float] = []
        with tempfile.TemporaryDirectory() as td:
            for idx_t, t_sec in enumerate(probe_times):
                f_jpg = str(Path(td) / f"hk_probe_{idx_t}.jpg")
                subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-y",
                        "-ss", f"{t_sec:.2f}",
                        "-i", target_mp4,
                        "-frames:v", "1",
                        "-q:v", "2",
                        f_jpg,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
                if not Path(f_jpg).exists() or Path(f_jpg).stat().st_size < 1_000:
                    continue
                if VisualCritic._is_solid_color_or_black(f_jpg):
                    return False, 0.0
                scores.append(float(VisualCritic._measure_sharpness(f_jpg)))
        if not scores:
            return False, 0.0
        sorted_sc = sorted(scores, reverse=True)
        # Lấy trung bình các khung hình nét nhất (bỏ qua 1 frame có thể rơi đúng nhịp chuyển cảnh/motion blur nhanh)
        top_k = sorted_sc[: max(1, len(sorted_sc) - 1)] if min(sorted_sc) >= 6.0 else sorted_sc
        eff_sharp = sum(top_k) / len(top_k) if min(sorted_sc) >= 6.0 else min(sorted_sc)
        return eff_sharp >= min_sharpness, round(eff_sharp, 2)

    try:
        passed_init, sharp_init = _probe_clip_sharpness(str(p_clip))
        if passed_init:
            return True, sharp_init

        cur_best_sharp = sharp_init
        # Chỉ áp dụng làm nét vi mô chuẩn Cinema Mastering nếu clip hơi mềm (< min_sharpness).
        for pass_idx, vf_enhance in enumerate(
            [
                "eq=brightness=0.02:contrast=1.06:saturation=1.04,unsharp=5:5:0.65:3:3:0.25",
                "eq=brightness=0.02:contrast=1.08:saturation=1.05,unsharp=5:5:0.95:3:3:0.45",
            ],
            start=1,
        ):
            tmp_enh = str(p_clip.with_name(f"{p_clip.stem}_sharp_enh_p{pass_idx}.mp4"))
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y",
                    "-i", str(p_clip),
                    "-vf", vf_enhance,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-color_range", "tv",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-c:a", "copy",
                    tmp_enh,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=40,
                check=False,
            )
            if Path(tmp_enh).exists() and Path(tmp_enh).stat().st_size > 8_000:
                passed_enh, sharp_enh = _probe_clip_sharpness(tmp_enh)
                if sharp_enh > cur_best_sharp:
                    shutil.move(tmp_enh, str(p_clip))
                    logger.info(
                        f"✨ [HookSharpnessGate Clean-Mastering P{pass_idx}] Đã tinh chỉnh độ nét tự nhiên cho Hook '{p_clip.name}': "
                        f"{cur_best_sharp:.2f} -> {sharp_enh:.2f} (yêu cầu >= {min_sharpness:.1f})"
                    )
                    cur_best_sharp = sharp_enh
                    if passed_enh:
                        return True, cur_best_sharp
                else:
                    Path(tmp_enh).unlink(missing_ok=True)
        return cur_best_sharp >= min_sharpness, cur_best_sharp
    except Exception as e_sh_gate:
        logger.debug(f"[HookSharpnessGate] Lỗi kiểm tra/nâng độ nét '{clip_path}': {e_sh_gate}")
        return False, 0.0


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

    video_format = str(
        state.get("video_format")
        or script_data.get("video_format")
        or ("long" if len(base_scenes) >= 14 else "short")
    ).strip().lower()
    if video_format == "long":
        logger.info(
            "🎬 [HookVideoNode] Chế độ Video Dài 16:9 (Long-Form): "
            "Bỏ qua săn YouTube Shorts dọc 9:16, chuyển sang luồng tải đồng loạt 16:9 Bilibili Safe-Crop & Documentary HD tại node_fetch_media."
        )
        return {
            "current_step": "HOOK_VIDEO_LONGFORM_BYPASS",
            "pre_resolved_hook_media": {},
            "progress_percent": 50,
            "progress_message": "🎬 Video Dài 16:9: Chuyển sang luồng săn đồng loạt 16:9 Bilibili Safe-Crop & Documentary HD...",
        }

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
    channel_id_norm = str(state.get("target_channel_id") or "").strip().lower()
    if str(niche).strip().lower() in ("psychology", "doodle_2d_explainer") or "psychology" in channel_id_norm:
        logger.info(
            "🎨 [HookVideoNode] Kênh Tâm lý học (2D Doodle Explainer): "
            "Bỏ qua săn video người thật/stock ở Cảnh 1-2 để đồng bộ 100% hoạt họa 2D Doodle từ giây 0.0s tại node_fetch_media."
        )
        return {
            "current_step": "HOOK_VIDEO_DOODLE_BYPASS",
            "pre_resolved_hook_media": {},
            "progress_percent": 50,
            "progress_message": "🎨 Kênh Psychology: Chuyển sang dựng đồng bộ 100% hoạt họa 2D Doodle từ Cảnh 1 (0.0s)...",
        }
    visual_keyword = scene_1.get("visual_keyword") or topic
    hook_narration = scene_1.get("narration") or audio_1.get("narration") or script_data.get("hook") or ""

    dur_s1 = _get_scene_dur_sec(1, default_s=3.5)
    dur_s2 = _get_scene_dur_sec(2, default_s=3.2) if len(base_scenes) >= 2 else 0.0
    # VideoAgent dùng xfade 0.16s mượt mà giữa các cảnh (transition_duration=0.16)
    # nên thời lượng clip Cảnh 1 & Cảnh 2 cộng sẵn 0.16s xfade overlap để khớp chính xác 100% lời đọc (0ms lệch phụ đề)
    transition_duration = 0.16 if len(base_scenes) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
    target_hook_duration_sec = dur_s1 + transition_duration
    span_opening_two_scenes = bool(len(base_scenes) >= 2)
    opening_hook_window_sec = round((dur_s1 + dur_s2) if span_opening_two_scenes else target_hook_duration_sec, 2)

    keys = settings.get_gemini_keys_pool() if hasattr(settings, "get_gemini_keys_pool") else []
    if not keys:
        raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    # ──────────────────────────────────────────────────────────────────────────
    # BƯỚC 1: CHUYÊN GIA LLM ĐỌC TOÀN BỘ KỊCH BẢN, CHẮT LỌC ĐIỂM NÓNG (HOT TOPIC ESSENCE) & TẠO 5 TRUY VẤN SĂN PEAK YT SHORTS
    # ──────────────────────────────────────────────────────────────────────────
    script_title = str(script_data.get("title") or topic or "").strip()
    full_script_text = " | ".join(
        f"Scene {s.get('scene_id', i + 1)}: {s.get('narration', '')} [kw: {s.get('visual_keyword', '')}]"
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

    hook_visual_mode: str = "video"
    is_abstract_keyword: bool = bool(pre_abstract) if isinstance(pre_abstract, bool) else (pre_mode == "scene")
    climax_scene_id: int = int(pre_climax_id) if isinstance(pre_climax_id, int) and 1 < pre_climax_id <= len(base_scenes) else default_climax_scene_id
    queries: List[str] = (
        [str(q).strip() for q in pre_queries if str(q).strip()][:5]
        if isinstance(pre_queries, list) and pre_queries
        else fallback_queries
    )
    climax_desc: str = f"{primary_entity or topic} — {visual_keyword}"
    visual_hook_blueprint: str = climax_desc
    hot_topic_essence: str = script_title or topic
    must_see_visual_cues: List[str] = []
    hook_production_strategy: str = "VIRAL_SHORT_ACTION_PEAK"
    beat_1_query: str = queries[0] if queries else visual_keyword
    beat_2_query: str = queries[1] if len(queries) > 1 else beat_1_query
    beat_1_subject: str = primary_entity or topic
    beat_2_subject: str = primary_entity or topic
    stock_broll_queries: List[str] = []
    atmosphere_fx: str = "action"
    is_3d_cgi_subject: bool = False

    rotator = KeyRotator(keys)

    @trace_custom_span("HookVideoNode.distill_script_hot_peaks_llm", run_type="llm")
    def _distill_script_hot_peaks_with_llm() -> Optional[Dict[str, Any]]:
        from app.services.niche_manager import build_llm_production_overview_prompt
        niche_yaml_blueprint = build_llm_production_overview_prompt(
            niche or "general",
            include_all_niches_map=False,
        )
        plan_prompt = f"""You are an elite Viral YouTube Shorts Peak Footage Scout & Script-to-Hook Visual Director.
Read the ENTIRE finalized script below from Scene 1 to Scene 8, consult the YAML NICHE PRODUCTION BLUEPRINT & ABSTRACT-TO-PHYSICAL QUERY TRANSLATIONS, distill the hottest core conflict/spectacle of the story, and engineer 5 hyper-targeted YouTube Shorts search queries to harvest the most explosive PEAK action clips for the Opening Hook (0.0–5.0s) and Mid-Video Climax (Scene {climax_scene_id}).

- Video Title: {script_title}
- Topic: {topic}
- Primary Subject: {primary_entity or topic}
- Channel Niche: {niche}
- Opening Hook Narration (Scene 1): {hook_narration}
- Scene 1 Visual Keyword: {visual_keyword}
- Pre-Suggested Script Queries: {json.dumps(queries, ensure_ascii=False)}
- Full Script (All Scenes): {full_script_text[:2400]}

{niche_yaml_blueprint}

CRITICAL PEAK HARVESTING & DIRECTORIAL RULES:
1. DISTILL THE HOTTEST VISUAL PAYOFF FROM THE ENTIRE SCRIPT:
   - Do NOT translate Scene 1 blindly if Scene 1 is rhetorical or abstract. Read all scenes and the YAML `abstract_to_physical_query_translations` to identify the #1 most shocking physical clash, high-speed strike, extreme natural phenomenon, or intense forensic/neuroscience/behavioral moment in the script, and bring that Peak right into the first 4–5 seconds (Visual Foreshadowing).
2. CHOOSE "hook_production_strategy" FROM EXACTLY ONE OF THESE 3 MODES:
   - "VIRAL_SHORT_ACTION_PEAK": Default #1 priority for wildlife predators, animal rescues, underwater/ocean phenomena, natural disasters, 3D CGI monsters, AND psychology/forensic/neuroscience topics where real YouTube Shorts peak clips (FBI interrogation room, courtroom reaction, 3D glowing brain MRI, iconic character cold stare) create an instant visceral shock.
   - "CINEMATIC_BROLL_FAST_CUT": Secondary fallback only when a quiet philosophical topic has no concrete YouTube Shorts visual spectacle.
   - "HYBRID_MOTION_AND_SCENE_MONTAGE": Best for counterfactual history ("What If"), ancient empires, or historical events.
3. ENGINEER 5 DUAL-TRACK YOUTUBE PEAK QUERIES ("search_queries" — STRICTLY GOVERNED BY THE INJECTED `YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY` ABOVE):
   - Write 5 distinct English search queries (3–5 words each) in authentic documentary/camera-recording phrasing (`[Concrete Subject] + [Real Physical Action/Environment]`), matching the `YAML Clean Single-Entity Real-Action Query Examples` and `YAML Direct-Encounter / Hook Query Examples` above.
   - YAML DUAL-TRACK "A vs B" STRATEGY (CRITICAL):
     * Follow the YAML `Dual-Track Clash vs. Single-Entity Rule`: when the script pits two entities against each other (`Entity A vs Entity B`) and the active niche allows real encounter probes, use at most Query 1–2 to probe GENUINE real-camera encounter footage (`"[Entity A] attacking [Entity B]"`, `"[Entity A] vs [Entity B]"` — NEVER `#edit` or `"who would win"`), AND dedicate Queries 3–5 to **CLEAN SINGLE-ENTITY REAL FOOTAGE** of each contender individually (`"[Entity A] + [real action/environment]"`, `"[Entity B] + [real action/environment]"`). That way our engine searches BOTH simultaneously and immediately Fast-Drops `"A vs B"` if no clean clash clip exists!
     * If the active niche's YAML `Dual-Track Clash vs. Single-Entity Rule` forbids `"vs"` in video searches, make ALL 5 queries 100% single-entity concrete physical queries!
   - For abstract psychological/historical/philosophical concepts: follow the YAML `abstract_to_physical_query_translations` and `Niche Anti-Flop Scene Continuity Guardrail` above.
   - Query 1 ("beat_1_query"): The ultimate 0.0–2.5s Opening Peak Shock (real clash or single-entity explosive action).
   - Query 2 ("beat_2_query"): Distinct close-up strike, weapon, or single-entity power shot for 2.5–5.0s.
   - Query 3: Single-entity A real physical action + natural environment (NO `"vs"`).
   - Query 4: Single-entity B (or Mid-Video Climax Scene {climax_scene_id}) real physical action + environment (NO `"vs"`).
   - Query 5: High-impact close-up / phenomenon angle (NO `"vs"`).
   - NEVER use abstract meta-words or any tag listed in `YAML Forbidden #Edit / Quiz / AI-Slop Video Markers`.
   - Also provide "must_see_visual_cues" (3–4 concrete visual elements that must appear in the Peak frames) and "stock_broll_queries" (2 concise 2–4 word SINGLE-ENTITY queries matching `YAML Canonical Stock/Fallback Query Examples` — NEVER include `"vs"` in `stock_broll_queries`).

Return ONLY valid JSON:
{{
  "canonical_subject": "bare 1-2 word noun without adjectives",
  "beat_1_subject": "bare 1-2 word subject noun for Beat 1",
  "beat_2_subject": "bare 1-2 word subject noun for Beat 2",
  "is_3d_cgi_subject": false,
  "hook_production_strategy": "VIRAL_SHORT_ACTION_PEAK | CINEMATIC_BROLL_FAST_CUT | HYBRID_MOTION_AND_SCENE_MONTAGE",
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "hot_topic_essence": "1-sentence distillation of the hottest viral spectacle in this script",
  "visual_hook_blueprint": "1-sentence description of the exact peak visual payoff that must appear in 0-3.5s",
  "must_see_visual_cues": ["concrete visual cue 1", "concrete visual cue 2", "concrete visual cue 3"],
  "beat_1_query": "concrete physical subject + peak action for 0.0-1.8s (3-5 words)",
  "beat_2_query": "distinct close-up/reaction/angle for 1.8-3.5s (3-5 words)",
  "stock_broll_queries": ["subject1 habitat/close up (2-3 words)", "subject2 habitat/close up (2-3 words)"],
  "climax_scene_id": {climax_scene_id},
  "climax_description": "1-sentence description of mid-video climax visual",
  "search_queries": ["peak query 1", "peak query 2", "peak query 3", "peak query 4", "peak query 5"],
  "atmosphere_fx": "action | psychology | mystery | history"
}}"""
        for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=5):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": plan_prompt}]}],
                    "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.15),
                }
                t_req0 = time.monotonic()
                resp = requests.post(url, json=payload, timeout=KeyRotator.fast_timeout(14))
                req_elapsed = time.monotonic() - t_req0
                if resp.status_code == 200:
                    rotator.record_model_success(key, model_name, req_elapsed)
                    cands = resp.json().get("candidates", [])
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            return json.loads(m.group(0))
                elif resp.status_code in (429, 403, 404, 500, 502, 503, 504, 400):
                    rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
            except Exception as e_distill:
                rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e_distill))
                logger.debug(f"[HookVideoNode] Script Hot-Peak Distiller fallback ({model_name}): {e_distill}")
        return None

    gemini_decided = False
    canonical_subject = ""
    distilled_data = _distill_script_hot_peaks_with_llm()
    if isinstance(distilled_data, dict) and distilled_data:
        canonical_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(distilled_data.get("canonical_subject") or "")).strip()
        beat_1_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(distilled_data.get("beat_1_subject") or canonical_subject or primary_entity or "")).strip()
        beat_2_subject = re.sub(r"[^a-zA-Z0-9\s]", " ", str(distilled_data.get("beat_2_subject") or canonical_subject or primary_entity or "")).strip()
        is_3d_cgi_subject = bool(distilled_data.get("is_3d_cgi_subject", False))
        strat_raw = str(distilled_data.get("hook_production_strategy") or "").strip().upper()
        if strat_raw in ("VIRAL_SHORT_ACTION_PEAK", "CINEMATIC_BROLL_FAST_CUT", "HYBRID_MOTION_AND_SCENE_MONTAGE"):
            hook_production_strategy = strat_raw
        mode_raw = str(distilled_data.get("hook_visual_mode") or "").strip().lower()
        abs_raw = bool(distilled_data.get("is_abstract_keyword", mode_raw == "scene"))
        # Luôn ưu tiên săn Video chuyển động thật (Dual-Beat Fast-Cut) cho Cảnh 1 trên cả 7 kênh;
        # chỉ fallback về 'scene' ở Bước 4 nếu toàn bộ ứng viên video không vượt qua cổng kiểm duyệt.
        hook_visual_mode = "video"
        is_abstract_keyword = abs_raw

        hot_topic_essence = str(distilled_data.get("hot_topic_essence") or hot_topic_essence).strip()
        raw_cues = distilled_data.get("must_see_visual_cues")
        if isinstance(raw_cues, list):
            must_see_visual_cues = [str(c).strip() for c in raw_cues if str(c).strip()][:4]

        b1_raw = re.sub(r"[^a-zA-Z0-9\s]", " ", str(distilled_data.get("beat_1_query") or "")).strip()
        b2_raw = re.sub(r"[^a-zA-Z0-9\s]", " ", str(distilled_data.get("beat_2_query") or "")).strip()
        raw_sb = [
            re.sub(r"[^a-zA-Z0-9\s]", " ", str(sq)).strip()
            for sq in (distilled_data.get("stock_broll_queries") or [])
            if str(sq).strip()
        ]
        stock_broll_queries = [re.sub(r"\s+", " ", sq).strip() for sq in raw_sb if len(sq.split()) >= 1][:3]
        raw_q = [
            re.sub(r"[^a-zA-Z0-9\s]", " ", str(q)).strip()
            for q in ([b1_raw, b2_raw] + list(distilled_data.get("search_queries") or []) + list(queries or []))
            if str(q).strip()
        ]
        dedup_q: List[str] = []
        for rq in raw_q:
            rq_c = re.sub(r"\s+", " ", rq).strip()
            if len(rq_c.split()) >= 2 and rq_c.lower() not in [x.lower() for x in dedup_q]:
                dedup_q.append(rq_c)
        if dedup_q:
            queries = dedup_q[:5]
            beat_1_query = queries[0]
            beat_2_query = queries[1] if len(queries) > 1 else queries[0]

        c_id_val = int(distilled_data.get("climax_scene_id") or climax_scene_id)
        if 1 < c_id_val <= len(base_scenes):
            climax_scene_id = c_id_val

        climax_desc = str(distilled_data.get("climax_description") or climax_desc).strip()
        visual_hook_blueprint = str(distilled_data.get("visual_hook_blueprint") or climax_desc).strip()
        atmosphere_fx = str(distilled_data.get("atmosphere_fx") or niche or "action").lower()
        gemini_decided = True

    if not gemini_decided:
        combined_text = f"{topic} {hook_narration} {visual_keyword} {niche}".lower()
        if any(k in combined_text for k in ("psych", "manipulat", "gaslight", "narcissist", "love bomb", "mindset", "body language", "liar", "interrogat")):
            hook_production_strategy = "CINEMATIC_BROLL_FAST_CUT"
            hook_visual_mode = "video"
            is_abstract_keyword = True
        elif any(k in combined_text for k in ("what if", "alternate history", "ancient", "empire", "world war", "soviet")):
            hook_production_strategy = "HYBRID_MOTION_AND_SCENE_MONTAGE"
            hook_visual_mode = "video"
            is_abstract_keyword = False
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
        has_versus_pattern,
        is_edit_or_ai_slop_video_metadata,
        build_single_entity_stock_queries,
    )
    from app.graph.evaluators.visual_critic import VisualCritic

    _COPYRIGHT_CLAIM_BLOCKLIST = (
        "all rights reserved", "licensed to", "jukin media", "viralhog", "newsflare",
        "caters news", "bviral", "storyful", "collab", "lpe360", "rm videos",
    )

    cand_seq_idx = 0
    # Dual-Track YouTube Hook Search: Giữ tối đa 2 truy vấn 'A vs B' (săn cảnh chạm trán thật) + dành ít nhất 3 nhánh cho Đơn Chủ Thể quay thật!
    raw_yt_q = list(queries[:5])
    single_hook_fallbacks = build_single_entity_stock_queries(
        raw_queries=[*stock_broll_queries, beat_1_subject, beat_2_subject, *raw_yt_q],
        subject_entity=canonical_subject or primary_entity,
        topic=topic,
        max_words=4,
    )
    yt_queries_to_run: List[str] = []
    vs_q_count = 0
    for q_item in [*raw_yt_q, *single_hook_fallbacks]:
        q_str = str(q_item or "").strip()
        if not q_str or q_str.lower() in {x.lower() for x in yt_queries_to_run}:
            continue
        if has_versus_pattern(q_str):
            if vs_q_count >= 2:
                continue
            vs_q_count += 1
        yt_queries_to_run.append(q_str)
        if len(yt_queries_to_run) >= 5:
            break
    subject_anchor_words = {
        w.lower()
        for src_s in (canonical_subject, beat_1_subject, beat_2_subject, beat_1_query, beat_2_query, *queries, *stock_broll_queries, primary_entity, topic)
        for w in re.findall(r"[a-zA-Z]{3,}", str(src_s or "").lower())
        if w.lower() not in {"the", "and", "for", "with", "from", "into", "that", "this", "why", "how", "what", "when", "fatal", "nature", "defies", "ambush", "secret", "truth", "footage", "action", "wildlife", "cinematic", "dramatic", "close", "shorts", "short"}
    }

    # ── BƯỚC 2A: QUÉT SONG SONG ĐA TRUY VẤN (KỆ SHORTS 9:16 + VIDEO GỐC 16:9 HD SẠCH CHỮ) & DÙNG LLM CHỌN TOP 8 CLIP PEAK ──
    pooled_flat_by_id: Dict[str, Dict[str, Any]] = {}

    def _run_flat_search_for_query(q_pair: tuple[int, str]) -> tuple[int, str, List[Dict[str, Any]]]:
        q_idx, q = q_pair
        raw_flat_cands: List[Dict[str, Any]] = []
        try:
            import yt_dlp
            # Bước 1: Quét trực tiếp kệ YouTube Shorts dọc 9:16 (`shortsLockupViewModel` / `/shorts/`)
            native_entries = license_policy.search_youtube_native_shorts_flat(q, max_results=12)
            seen_q_ids = {str(x.get("id") or "") for x in native_entries if x.get("id")}
            combined_entries: List[Dict[str, Any]] = list(native_entries)

            # Bước 2 (Luôn bổ sung song song): Quét cả video YouTube thường 16:9 HD/4K quay thật (không khóa ở < 8)
            flat_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "skip_download": True,
                "socket_timeout": 8,
                "ignoreerrors": True,
            }
            with yt_dlp.YoutubeDL(license_policy.youtube_search_opts(flat_opts, max_results=10)) as ydl_flat:
                flat_res = ydl_flat.extract_info(license_policy.youtube_search_target(q, max_results=10), download=False)
            for e_flat_item in ((flat_res or {}).get("entries") or []):
                if isinstance(e_flat_item, dict):
                    eid = str(e_flat_item.get("id") or "").strip()
                    if eid and eid not in seen_q_ids:
                        seen_q_ids.add(eid)
                        e_flat_item["is_vertical_short"] = license_policy.is_entry_vertical_short(e_flat_item)
                        combined_entries.append(e_flat_item)

            for entry in combined_entries:
                if not isinstance(entry, dict):
                    continue
                vid_id = str(entry.get("id") or "").strip()
                if not vid_id:
                    continue
                is_vert_short = license_policy.is_entry_vertical_short(entry)
                dur_e = float(entry.get("duration") or (25.0 if is_vert_short else 0.0))
                if dur_e <= 0 or dur_e < 4 or dur_e > 180:
                    continue
                title_e = str(entry.get("title") or "")
                uploader_e = str(entry.get("uploader") or entry.get("channel") or "")
                if license_policy._VN_EXCLUSIVE_DIACRITIC_RE.search(f"{title_e} {uploader_e}"):
                    continue
                meta_e = f"{title_e} {uploader_e}".lower()
                if (
                    any(bad in meta_e for bad in _TALKING_HEAD_BLOCKLIST)
                    or any(bad_c in meta_e for bad_c in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST)
                    or any(cp_w in meta_e for cp_w in _COPYRIGHT_CLAIM_BLOCKLIST)
                    or is_edit_or_ai_slop_video_metadata(
                        title_e,
                        uploader_e,
                        allow_3d_cgi_monster=bool(is_3d_cgi_subject or is_scifi_pre),
                        channel_niche=niche,
                    )
                ):
                    continue
                title_low = title_e.lower()
                q_words = {
                    w.lower()
                    for w in re.findall(r"[a-zA-Z]{3,}", q.lower())
                    if w.lower() not in {"the", "and", "for", "with", "shorts", "short", "video", "real"}
                }
                anchor_hits = sum(1 for aw in (subject_anchor_words | q_words) if aw in title_low)
                if (
                    (subject_anchor_words or q_words)
                    and anchor_hits == 0
                    and hook_production_strategy != "CINEMATIC_BROLL_FAST_CUT"
                    and "psych" not in niche.lower()
                ):
                    continue
                vid_url = (
                    f"https://www.youtube.com/shorts/{vid_id}"
                    if is_vert_short
                    else f"https://www.youtube.com/watch?v={vid_id}"
                )
                views_e = int(entry.get("view_count") or 0)
                viral_boost = (
                    85.0 if views_e >= 10_000_000 else
                    60.0 if views_e >= 1_000_000 else
                    30.0 if views_e >= 250_000 else
                    12.0 if views_e >= 50_000 else 0.0
                )
                short_fmt_bonus = 20.0 if (0 < dur_e <= 75) else (10.0 if dur_e <= 150 else 0.0)
                vertical_native_bonus = 25.0 if is_vert_short else 0.0
                clean_title_bonus = 25.0 if title_e.count("#") <= 2 else (-20.0 if title_e.count("#") >= 5 else 0.0)
                peak_intensity_bonus = (
                    42.0
                    if re.search(
                        r"\b(?:attack|ambush|hunt|hunting|strike|bite|catch|fight|chase|jump|leap|dive|diving|roar|eruption|explosion|shockwave|rescue|saved|closeup|close\-up|encounter|wild|underwater|deep\s+sea|sonar)\b",
                        title_low,
                    )
                    else 0.0
                )
                passive_clip_penalty = (
                    -65.0
                    if re.search(
                        r"\b(?:sleeping|napping|relaxing|ambient|ASMR|zoo\s+tour|petting\s+zoo|feeding\s+time|slideshow|wallpaper|screensaver|1\s+hour|10\s+hours)\b",
                        title_low,
                        re.IGNORECASE,
                    )
                    else 0.0
                )
                raw_flat_cands.append({
                    "id": vid_id,
                    "url": vid_url,
                    "title": title_e,
                    "uploader": uploader_e,
                    "duration": dur_e,
                    "view_count": views_e,
                    "is_vertical_short": is_vert_short,
                    "matched_query": q,
                    "q_idx": q_idx,
                    "score": (
                        (anchor_hits * 45.0)
                        + peak_intensity_bonus
                        + passive_clip_penalty
                        + min(views_e / 15000.0, 75.0)
                        + viral_boost
                        + short_fmt_bonus
                        + vertical_native_bonus
                        + clean_title_bonus
                    ),
                })
        except Exception as e_flat:
            logger.debug(f"[HookVideoNode] yt_dlp flat pre-filter skip ({q}): {e_flat}")
        return q_idx, q, raw_flat_cands

    if yt_queries_to_run:
        with ThreadPoolExecutor(max_workers=min(5, len(yt_queries_to_run))) as flat_pool:
            flat_results = list(flat_pool.map(_run_flat_search_for_query, enumerate(yt_queries_to_run)))
        for q_idx, q, raw_flat_cands in sorted(flat_results, key=lambda x: x[0]):
            if not raw_flat_cands:
                continue
            try:
                ranked_flat = rag.rank_video_candidates_for_scene(
                    job_id=job_id,
                    candidates=raw_flat_cands,
                    scene_narration=hook_narration,
                    scene_query=q,
                    topic=topic,
                    subject_entity=canonical_subject or primary_entity or climax_desc,
                    channel_id=channel_id,
                )
                for rc in ranked_flat[:6]:
                    cid_rc = rc["id"]
                    candidate_titles[cid_rc] = rc.get("title", "")
                    candidate_uploaders[cid_rc] = rc.get("uploader", "")
                    candidate_views[cid_rc] = int(rc.get("view_count") or 0)
                    if cid_rc not in pooled_flat_by_id or float(rc.get("score", 0)) > float(pooled_flat_by_id[cid_rc].get("score", 0)):
                        pooled_flat_by_id[cid_rc] = rc
            except Exception as e_rank:
                logger.debug(f"[HookVideoNode] RAG flat rank skip ({q}): {e_rank}")

    pooled_cands_sorted = sorted(
        pooled_flat_by_id.values(),
        key=lambda x: float(x.get("rag_score") or x.get("score") or 0.0),
        reverse=True,
    )[:22]

    @trace_custom_span("HookVideoNode.select_hottest_yt_shorts_llm", run_type="llm")
    def _select_hottest_yt_shorts_with_llm(candidates_pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(candidates_pool) <= 3:
            return candidates_pool
        compact_list = [
            {
                "id": c["id"],
                "title": str(c.get("title") or "")[:95],
                "views": int(c.get("view_count") or 0),
                "duration_s": round(float(c.get("duration") or 0), 1),
                "format": "9:16_NATIVE_SHORT" if c.get("is_vertical_short") else "16:9_LANDSCAPE",
                "channel": str(c.get("uploader") or "")[:40],
                "query": str(c.get("matched_query") or ""),
            }
            for c in candidates_pool
        ]
        from app.services.niche_manager import build_video_search_strategy_prompt_block
        yaml_scout_rules = build_video_search_strategy_prompt_block(niche or "general")
        scout_prompt = f"""You are an elite YouTube Peak Action Scout & Hook Producer.
We have a finalized script and {len(compact_list)} candidate YouTube videos (both 9:16 Shorts and 16:9 raw documentary/field recordings) found across our Dual-Track queries.
Your job is to read the script, the active YAML VIDEO SEARCH STRATEGY, and the candidate titles/metadata, and select the TOP 6 YouTube videos that are most likely to contain CLEAN, UNEDITED, JAW-DROPPING REAL PEAK FOOTAGE for our Opening Hook (0.0–3.5s) and Mid-Video Climax (Scene {climax_scene_id}).

{yaml_scout_rules}

- Script Title: {script_title}
- Hot Topic Essence: {hot_topic_essence}
- Opening 0-3.5s Peak Blueprint: {visual_hook_blueprint}
- Mid-Video Climax Blueprint (Scene {climax_scene_id}): {climax_desc}
- Must-See Visual Cues: {json.dumps(must_see_visual_cues, ensure_ascii=False)}
- Full Script: {full_script_text[:1800]}

CANDIDATE YOUTUBE VIDEOS POOL:
{json.dumps(compact_list, ensure_ascii=False, indent=2)}

SELECTION RULES:
1. CLEAN RAW FOOTAGE OVER EDITED SHORTS (CRITICAL):
   - Prioritize CLEAN, AUTHENTIC, UNEDITED RAW FOOTAGE (both `"9:16_NATIVE_SHORT"` and `"16:9_LANDSCAPE"` wildlife/field/documentary clips—our FFmpeg engine smart-crops 16:9 cleanly!) OVER heavily-edited comparison `#shorts`, `"Who would win"` debates, or AI-generated Veo/Sora clips!
   - DUAL-TRACK "A vs B" RULE: Include at most 1–2 `"A vs B"` candidates ONLY if they look like GENUINE real-camera encounter recordings. Always include clean single-entity real-action clips of the individual contenders so if `"A vs B"` is dirty/edited, we immediately have clean single-entity Peak footage!
2. Prioritize candidates whose titles describe REAL, HIGH-INTENSITY PHYSICAL ACTION, CLOSE-UP STRIKE, or SHOCKING NATURAL PHENOMENA matching "{hot_topic_essence}" and "{visual_hook_blueprint}".
3. Reject comparison `#edit` videos, `"Who would win"` quizzes, AI-generated clips (unless 3D CGI monster is requested), clickbait commentary, reactions, podcasts, toys/figurines, or passive zoo/sleeping clips.
4. Pick up to 6 best video IDs ordered from #1 strongest Opening Hook Peak to #6.

Return ONLY valid JSON:
{{
  "selected_ids": ["id1", "id2", "id3", "id4", "id5", "id6"],
  "selection_reasoning": "1-sentence explanation of why #1 and #2 were chosen for the Peak Hook"
}}"""
        for m_scout, k_scout in rotator.iter_best_model_key_pairs(max_total_attempts=4):
            try:
                u_sc = f"https://generativelanguage.googleapis.com/v1beta/models/{m_scout}:generateContent?key={k_scout}"
                p_sc = {
                    "contents": [{"parts": [{"text": scout_prompt}]}],
                    "generationConfig": KeyRotator.build_fast_json_gen_config(m_scout, temperature=0.15),
                }
                t_req_sc = time.monotonic()
                r_sc = requests.post(u_sc, json=p_sc, timeout=KeyRotator.fast_timeout(12))
                sc_elapsed = time.monotonic() - t_req_sc
                if r_sc.status_code == 200:
                    rotator.record_model_success(k_scout, m_scout, sc_elapsed)
                    cands_sc = r_sc.json().get("candidates", [])
                    if cands_sc:
                        txt_sc = "".join(p.get("text", "") for p in cands_sc[0].get("content", {}).get("parts", []))
                        m_sc = re.search(r"\{.*\}", txt_sc, re.DOTALL)
                        if m_sc:
                            d_sc = json.loads(m_sc.group(0))
                            sel_ids = [str(x).strip() for x in (d_sc.get("selected_ids") or []) if str(x).strip()]
                            id_map = {c["id"]: c for c in candidates_pool}
                            ordered_sel = [id_map[sid] for sid in sel_ids if sid in id_map]
                            for c_rem in candidates_pool:
                                if c_rem["id"] not in {x["id"] for x in ordered_sel}:
                                    ordered_sel.append(c_rem)
                            if ordered_sel:
                                logger.info(
                                    f"🔥 [HookVideoNode LLM Peak Scout] Đã duyệt {len(candidates_pool)} YouTube Shorts -> "
                                    f"Chọn Top {min(8, len(ordered_sel))}: {[c['title'][:38] for c in ordered_sel[:4]]} | "
                                    f"Lý do: {str(d_sc.get('selection_reasoning', ''))[:100]}"
                                )
                                return ordered_sel[:8]
                elif r_sc.status_code in (429, 403, 404, 500, 502, 503, 504, 400):
                    rotator.mark_model_rate_limited(k_scout, m_scout, status_code=r_sc.status_code, error_text=r_sc.text[:300])
            except Exception as e_sc:
                rotator.mark_model_rate_limited(k_scout, m_scout, status_code=503, error_text=str(e_sc))
                logger.debug(f"[HookVideoNode] LLM Peak Scout fallback ({m_scout}): {e_sc}")
        return candidates_pool[:8]

    top_curated_yt_cands = _select_hottest_yt_shorts_with_llm(pooled_cands_sorted) if pooled_cands_sorted else []

    def _download_and_extract_single_short(dl_pair: tuple[int, Dict[str, Any]]) -> List[tuple[int, str, Dict[str, Any]]]:
        dl_idx, rc_cand = dl_pair
        results_for_cand: List[tuple[int, str, Dict[str, Any]]] = []
        vid_url_dl = rc_cand["url"]
        q_idx = int(rc_cand.get("q_idx") or dl_idx)
        out_tmpl = str(hook_cand_dir / f"yt_hook_cand_{dl_idx}_%(id)s.%(ext)s")
        import time as _hk_time
        t_hk_dl_start = _hk_time.monotonic()

        def _hook_fast_switch_progress(d: Dict[str, Any]) -> None:
            if d.get("status") == "downloading" and (_hk_time.monotonic() - t_hk_dl_start) > 10.0:
                raise TimeoutError(f"Hook candidate download > 10s for {vid_url_dl} -> fast-switch")

        dl_opts = license_policy.optimize_ytdlp_download_opts({
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 8,
            "retries": 1,
            "nopart": True,
            "max_filesize": 18 * 1024 * 1024,
            "format": license_policy.VERTICAL_FIRST_YTDLP_FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": out_tmpl,
            "ignoreerrors": False,
            "progress_hooks": [_hook_fast_switch_progress],
        })
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                ydl_dl.download([vid_url_dl])
        except Exception as e_yt:
            logger.debug(f"[HookVideoNode] yt-dlp download skip ({vid_url_dl}): {e_yt}")

        for mp4_sub_idx, mp4 in enumerate(sorted(hook_cand_dir.glob(f"yt_hook_cand_{dl_idx}_{rc_cand['id']}.*"))):
            if mp4.suffix.lower() not in (".mp4", ".webm", ".mkv", ".mov"):
                continue
            v_str = str(mp4)
            try:
                if mp4.stat().st_size <= 60_000:
                    continue
            except Exception:
                continue

            peak_offset_sec, peak_duration_sec = find_hook_peak_window(
                v_str,
                min_duration=max(target_hook_duration_sec, 4.5),
                max_duration=15.0,
            )
            pre_clean_dur = min(max(peak_duration_sec, opening_hook_window_sec + 1.0, 5.2), 9.5)
            clean_peak_path = str(hook_cand_dir / f"hook_peak_clean_yt_{dl_idx}_{mp4_sub_idx}_{rc_cand['id']}.mp4")
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
                    min_duration=max(target_hook_duration_sec, 4.5),
                    max_duration=15.0,
                    exclude_ranges=alt_excl,
                )
                if abs(alt_offset - peak_offset_sec) >= 2.0:
                    try:
                        Path(clean_peak_path).unlink(missing_ok=True)
                        trim_video(
                            input_path=v_str,
                            duration_seconds=min(max(alt_dur, opening_hook_window_sec + 1.0, 5.2), 9.5),
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
                results_for_cand.append((dl_idx, v_str, {"passed": False}))
                continue

            results_for_cand.append((
                dl_idx,
                v_str,
                {
                    "passed": True,
                    "peak_offset_sec": peak_offset_sec,
                    "peak_duration_sec": peak_duration_sec,
                    "clean_peak_path": clean_peak_path,
                    "inspect_target": inspect_target,
                    "title": str(rc_cand.get("title") or ""),
                },
            ))
        return results_for_cand

    if top_curated_yt_cands and len(candidate_videos) < 8:
        # Dual-Track Wave 1: Tối đa 2 ứng viên 'A vs B' + ít nhất 2 ứng viên ĐƠN CHỦ THỂ quay thật ngay trong Wave 1;
        # nếu Wave 1 không ra đủ clip sạch -> Fast-Drop toàn bộ 'A vs B' ở Wave 2 để chỉ tải clip đơn chủ thể!
        wave1_selected: List[Dict[str, Any]] = []
        wave1_ids: set = set()
        vs_in_w1 = 0
        for cand_w in top_curated_yt_cands:
            is_vs_w = has_versus_pattern(cand_w.get("title", "")) or has_versus_pattern(cand_w.get("matched_query", ""))
            if is_vs_w:
                if vs_in_w1 >= 2:
                    continue
                vs_in_w1 += 1
            wave1_selected.append(cand_w)
            wave1_ids.add(cand_w.get("id"))
            if len(wave1_selected) >= 4:
                break
        if len(wave1_selected) < 4:
            for cand_w in top_curated_yt_cands:
                if cand_w.get("id") not in wave1_ids:
                    wave1_selected.append(cand_w)
                    wave1_ids.add(cand_w.get("id"))
                if len(wave1_selected) >= 4:
                    break

        wave1_cands = list(enumerate(wave1_selected))
        with ThreadPoolExecutor(max_workers=min(4, len(wave1_cands))) as dl_pool:
            dl_batches = list(dl_pool.map(_download_and_extract_single_short, wave1_cands))
        for batch in dl_batches:
            for _dl_i, v_str, peak_meta in batch:
                if v_str in validated_peaks or v_str in candidate_videos:
                    continue
                validated_peaks[v_str] = peak_meta
                if peak_meta.get("passed") and len(candidate_videos) < 8:
                    candidate_videos.append(v_str)
        if len(candidate_videos) < 2 and len(top_curated_yt_cands) > len(wave1_selected):
            # Fast-Drop 'A vs B' ở Wave 2: Bỏ qua các video 'vs' còn lại, chỉ tải clip đơn chủ thể sạch!
            rem_non_vs = [
                c for c in top_curated_yt_cands
                if c.get("id") not in wave1_ids and not has_versus_pattern(c.get("title", ""))
            ]
            if not rem_non_vs:
                rem_non_vs = [c for c in top_curated_yt_cands if c.get("id") not in wave1_ids]
            wave2_cands = list(enumerate(rem_non_vs[:4], start=4))
            with ThreadPoolExecutor(max_workers=min(4, len(wave2_cands))) as dl_pool2:
                dl_batches2 = list(dl_pool2.map(_download_and_extract_single_short, wave2_cands))
            for batch in dl_batches2:
                for _dl_i, v_str, peak_meta in batch:
                    if v_str in validated_peaks or v_str in candidate_videos:
                        continue
                    validated_peaks[v_str] = peak_meta
                    if peak_meta.get("passed") and len(candidate_videos) < 8:
                        candidate_videos.append(v_str)
        cand_seq_idx = min(8, len(top_curated_yt_cands)) + 10

    # ── TẦNG DỰ PHÒNG SỐ 2 (CHỈ KHI 100% YOUTUBE TẦNG 1 KHÔNG TÌM ĐƯỢC CLIP NÀO): PEXELS HD + PIXABAY HD + WIKIMEDIA ──
    need_multi_source_broll = (
        not is_3d_cgi_subject
        and len(candidate_videos) == 0
    )
    if need_multi_source_broll:
        try:
            from app.agents.media_agent import MediaAgent
            # Fast-Drop 'A vs B' tuyệt đối khi xuống kho Pexels/Pixabay: chỉ giữ truy vấn ĐƠN CHỦ THỂ!
            multi_beat_queries = build_single_entity_stock_queries(
                raw_queries=[*stock_broll_queries, beat_1_subject, beat_2_subject, beat_1_query, beat_2_query],
                subject_entity=canonical_subject or primary_entity,
                topic=topic,
                max_words=4,
            )
            if not multi_beat_queries:
                multi_beat_queries = [canonical_subject or visual_keyword or topic]

            combined_hook_subjects = (
                f"{beat_1_subject} / {beat_2_subject}"
                if beat_1_subject and beat_2_subject and beat_1_subject.lower() != beat_2_subject.lower()
                else (canonical_subject or primary_entity or topic)
            )
            max_broll_q = 4
            broll_queries_to_run = list(enumerate(multi_beat_queries[:max_broll_q]))

            def _fetch_single_multisrc_beat(mb_pair: tuple[int, str]) -> Optional[tuple[int, str, str, Dict[str, Any], Dict[str, Any]]]:
                mb_idx, st_q_hook = mb_pair
                try:
                    m_agent = MediaAgent()
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
                    if not v_url_ms:
                        return None
                    seq_id = cand_seq_idx + mb_idx
                    multi_v_path = str(hook_cand_dir / f"multisrc_hook_cand_{seq_id}.mp4")
                    m_agent.download_file(v_url_ms, multi_v_path)
                    if Path(multi_v_path).exists() and Path(multi_v_path).stat().st_size > 40_000:
                        p_off, p_dur = find_hook_peak_window(
                            multi_v_path,
                            min_duration=max(target_hook_duration_sec, 3.0),
                            max_duration=15.0,
                        )
                        clean_ms_path = str(hook_cand_dir / f"hook_peak_clean_ms_{seq_id}.mp4")
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
                            insp_ms, min_short_edge=720, min_sharpness=7.2, check_watermark=True, allow_snow=True, auto_sanitize=True
                        )
                        if ms_gate.get("passed", False):
                            peak_meta_ms = {
                                "passed": True,
                                "peak_offset_sec": p_off,
                                "peak_duration_sec": p_dur,
                                "clean_peak_path": clean_ms_path,
                                "inspect_target": insp_ms,
                                "pre_curated_stock": True,
                                "vision_score": float(best_multi_v.get("vision_score") or 8.2),
                            }
                            return mb_idx, st_q_hook, multi_v_path, best_multi_v, peak_meta_ms
                except Exception as e_b_worker:
                    logger.debug(f"[HookVideoNode] Multi-source beat worker skip ({st_q_hook}): {e_b_worker}")
                return None

            if broll_queries_to_run:
                with ThreadPoolExecutor(max_workers=min(4, len(broll_queries_to_run))) as ms_pool:
                    ms_results = list(ms_pool.map(_fetch_single_multisrc_beat, broll_queries_to_run))
                seen_multi_urls: set = set()
                for ms_item in ms_results:
                    if not ms_item or len(candidate_videos) >= 6:
                        continue
                    mb_idx, st_q_hook, multi_v_path, best_multi_v, peak_meta_ms = ms_item
                    v_url_ms = str(best_multi_v.get("url") or "")
                    if v_url_ms in seen_multi_urls:
                        continue
                    seen_multi_urls.add(v_url_ms)
                    validated_peaks[multi_v_path] = peak_meta_ms
                    candidate_videos.append(multi_v_path)
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
    _early_stop_lock = threading.Lock()
    _strong_passing_count = 0

    def _grade_single_hook_candidate(idx_pair: tuple[int, str]) -> Optional[tuple[int, Dict[str, Any]]]:
        nonlocal _strong_passing_count
        idx, v_path = idx_pair
        with _early_stop_lock:
            if _strong_passing_count >= 2:
                return None
        pre_v = validated_peaks.get(v_path)
        if pre_v and pre_v.get("passed"):
            peak_offset_sec = float(pre_v["peak_offset_sec"])
            peak_duration_sec = float(pre_v["peak_duration_sec"])
            clean_peak_path = str(pre_v["clean_peak_path"])
            inspect_target = str(pre_v["inspect_target"])
        else:
            # 3.1. Quét toàn bộ video Short để tìm đúng mốc Peak & đo trọn vẹn độ dài đoạn Peak tự nhiên (tới 15s)
            peak_offset_sec, peak_duration_sec = find_hook_peak_window(
                v_path,
                min_duration=max(target_hook_duration_sec, 4.5),
                max_duration=15.0,
            )

            # 3.1b. Cắt riêng đoạn Peak (kèm fair_use_transform để gọt lề chữ trên/dưới) rồi chạy cổng 3 bước (Cắt né -> Delogo/Crop -> Bỏ)
            _m_ext_yt = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(v_path).name)
            _ext_yt_sfx = f"_{_m_ext_yt.group(1)}" if _m_ext_yt else ""
            clean_peak_path = str(hook_cand_dir / f"hook_peak_clean_ext_{idx}{_ext_yt_sfx}.mp4")
            try:
                trim_video(
                    input_path=v_path,
                    duration_seconds=min(max(peak_duration_sec, opening_hook_window_sec + 1.0, 5.2), 9.5),
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
                min_sharpness=7.2,
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
                return None

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
            return None

        # 3.3. Gửi 3 khung hình tại đoạn Peak cho Gemini Vision chấm điểm & quyết định nên dùng "video" hay "scene"
        vision_prompt = f"""You are a strict YouTube Shorts Hook Video Quality Grader & Scroll-Stop Director.
Evaluate this 3-frame sequence extracted directly from the PEAK CLIMAX window (starting at {peak_offset_sec:.1f}s, natural peak length {peak_duration_sec:.1f}s) of a candidate Short video for:
- Video Topic: "{topic}"
- Hot Topic Essence: "{hot_topic_essence}"
- Target Opening Hook Blueprint: "{visual_hook_blueprint}"
- Desired Peak Visual Cues: {json.dumps(must_see_visual_cues, ensure_ascii=False)}
- Valid Subjects/Entities for Hook: "{beat_1_subject}, {beat_2_subject}, or {primary_entity}"
- Opening Hook Line: "{hook_narration}"
- Hook Production Strategy: "{hook_production_strategy}"

CRITICAL GRADING CALIBRATION (REAL VIDEO HOOK PRIORITY & PEAK INTENSITY):
- Real moving video footage of ANY of the core subjects ("{beat_1_subject}", "{beat_2_subject}", or "{primary_entity}") in their authentic natural environment/habitat (stalking, swimming, prowling, hunting, close-up detail, or dramatic motion) is ALWAYS 10x better for a YouTube Shorts Hook than downgrading to static photos ("scene")!
- For Psychology / Human Behavior topics (Niche="{niche}" or Strategy="CINEMATIC_BROLL_FAST_CUT"), valid visual subjects include cinematic human characters/actors, intense eye contact, cold micro-expressions, interrogation body language, or dramatic interpersonal tension matching "{visual_hook_blueprint}" (score entity_match_score 8.0–9.5 if it shows dramatic human behavior/expression without podcast mics or burned-in subtitles).
- Give the HIGHEST scores (8.8–10.0) on "motion_action_score" and "scroll_stop_score" when the frames show high-intensity peak action, close-up confrontation, or dramatic movement matching "{visual_hook_blueprint}".
- DO NOT reject a real wild video just because it shows stalking/prowling/swimming rather than a literal movie-like explosion/strike, as we combine multiple video beats in a fast-cut!
- STRICT ANTI-SUBTITLE & WATERMARK GATE: If ANY frame contains burned-in subtitles (especially Vietnamese text, TikTok/CapCut captions, or foreign text banners), giant text overlays, or channel watermarks, you MUST set "clean_frame_score" <= 3.0 and "recommended_hook_mode": "scene"!
- ONLY return "recommended_hook_mode": "scene" (rejecting the video) if the footage shows: (a) the WRONG species/subject not in "{topic}", (b) an animal trapped in an artificial zoo enclosure/concrete wall/cage when wild nature is required, (c) a contradicting biome/season (e.g. snow when tropical jungle is required), (d) a talking head/podcast/vlog/amateur webcam selfie speaking into a mic or isolated disembodied macro eyeball closeup, or (e) burned-in subtitles, Vietnamese/foreign text captions, giant text overlays, or watermarks.

Score strictly from 1.0 to 10.0:
1. "recommended_hook_mode": "video" if it shows any valid subject from "{topic}" in an authentic setting with clean frames; "scene" ONLY if it violates (a)-(e) above.
2. "entity_match_score": Does the clip show a valid subject from "{topic}" in an authentic natural habitat/setting? (7.5-10 = authentic subject in wild/natural habitat; <= 4.0 = wrong species, zoo cage/artificial wall, or contradicting biome).
3. "motion_action_score": Is it real footage with physical movement/life rather than a talking head or static slide? (7.5-10 = real wildlife/cinematic footage; 1-3 = talking head or static slide).
4. "scroll_stop_score": Visual quality & intrigue for a fast-cut Hook (7.0-10 = clear, engaging footage of the subject; <= 5.0 = blurry, obstructed, or boring empty background).
5. "clean_frame_score": Is the frame 100% clean without burned-in subtitles, Vietnamese/TikTok captions, text overlays, split-screen collages, or watermarks? (8.0-10 = clean footage; <= 3.0 = ANY visible burned-in subtitles, captions, or watermark/logo).
6. "reason": Short 1-sentence explanation.

Return ONLY valid JSON:
{{"recommended_hook_mode": "video", "entity_match_score": 8.5, "motion_action_score": 8.5, "scroll_stop_score": 8.2, "clean_frame_score": 8.5, "reason": "..."}}"""

        score_res: Optional[Dict[str, Any]] = None
        for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=5):
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
                    ],
                    "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1),
                }
                t_req_v1 = time.monotonic()
                resp = requests.post(url, json=payload, timeout=(3.5, 8.5))
                v1_elapsed = time.monotonic() - t_req_v1
                if resp.status_code == 200:
                    rotator.record_model_success(key, model_name, v1_elapsed)
                    cands = resp.json().get("candidates", [])
                    if cands:
                        txt = "".join(
                            p.get("text", "")
                            for p in cands[0].get("content", {}).get("parts", [])
                            if isinstance(p, dict) and not p.get("thought")
                        )
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
                                (rec_mode == "video")
                                and (overall >= 6.2)
                                and (e_score >= 5.8)
                                and (c_score >= 6.8)
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
                elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                    rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
            except Exception as e_vis:
                rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e_vis))
                logger.debug(f"[HookVideoNode] Gemini Vision Peak score error ({model_name}): {e_vis}")

        # Nếu cửa sổ Peak #1 của video Short chưa đạt (do đoạn đầu bị khuất/chưa xuất hiện chủ thể hoặc chưa tới đoạn cao trào):
        # Tự động quét cửa sổ Peak #2 (loại trừ vùng Peak #1) ngay trong cùng video Short đó và kiểm định lại bằng Gemini Vision!
        _skip_w2 = False
        if score_res is not None and score_res.get("passed") and float(score_res.get("overall_score", 0.0)) >= 8.0:
            with _early_stop_lock:
                _strong_passing_count += 1
        elif score_res is not None and not score_res.get("passed"):
            with _early_stop_lock:
                _skip_w2 = _strong_passing_count >= 2
        if score_res is not None and not score_res.get("passed") and not _skip_w2:
            try:
                alt_excl_v = [(max(0.0, peak_offset_sec - 1.5), peak_offset_sec + 5.5)]
                alt_off2, alt_dur2 = find_hook_peak_window(
                    v_path,
                    min_duration=max(target_hook_duration_sec, 4.5),
                    max_duration=15.0,
                    exclude_ranges=alt_excl_v,
                )
                if abs(alt_off2 - peak_offset_sec) >= 2.0:
                    _m_alt_yt = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(v_path).name)
                    _alt_yt_sfx = f"_{_m_alt_yt.group(1)}" if _m_alt_yt else ""
                    alt_clean_path = str(hook_cand_dir / f"hook_peak_clean_alt2_{idx}{_alt_yt_sfx}.mp4")
                    trim_video(
                        input_path=v_path,
                        duration_seconds=min(max(alt_dur2, opening_hook_window_sec + 1.0, 5.2), 9.5),
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
                                        p2 = {
                                            "contents": [
                                                {
                                                    "parts": [
                                                        {"text": vision_prompt},
                                                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_alt2}},
                                                    ]
                                                }
                                            ],
                                            "generationConfig": KeyRotator.build_fast_json_gen_config(m_n2, temperature=0.1),
                                        }
                                        t_req_v2 = time.monotonic()
                                        r2 = requests.post(u2, json=p2, timeout=(3.5, 8.5))
                                        v2_elapsed = time.monotonic() - t_req_v2
                                        if r2.status_code == 200:
                                            rotator.record_model_success(k_2, m_n2, v2_elapsed)
                                            c2 = r2.json().get("candidates", [])
                                            if c2:
                                                t2 = "".join(
                                                    p.get("text", "")
                                                    for p in c2[0].get("content", {}).get("parts", [])
                                                    if isinstance(p, dict) and not p.get("thought")
                                                )
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
                                                        (rec2 == "video")
                                                        and (ov2 >= 6.2)
                                                        and (e2 >= 5.8)
                                                        and (c2_s >= 6.8)
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
                                        elif r2.status_code in (429, 403, 404, 500, 502, 503, 504):
                                            rotator.mark_model_rate_limited(k_2, m_n2, status_code=r2.status_code, error_text=r2.text[:300])
                                    except Exception as e_v2:
                                        rotator.mark_model_rate_limited(k_2, m_n2, status_code=503, error_text=str(e_v2))
                                        continue
            except Exception as e_alt2:
                logger.debug(f"[HookVideoNode] Window #2 retry skip: {e_alt2}")

        if score_res is None:
            cand_title_low = str((pre_v or {}).get("title") or "").lower()
            subj_tokens = [
                tok
                for tok in re.findall(
                    r"[a-z]{4,}",
                    f"{beat_1_subject} {beat_2_subject} {primary_entity} {canonical_subject}".lower(),
                )
                if tok not in {"with", "from", "that", "this", "into", "over", "under", "most", "wild", "real", "video", "short", "shorts"}
            ]
            if pre_v and pre_v.get("passed") and subj_tokens and any(t in cand_title_low for t in subj_tokens):
                logger.info(
                    f"🛡️ [HookVideoGrader Outage-Rescue] Gemini Vision tạm chạm trần 429/503, nhưng '{Path(v_path).name}' "
                    f"đã vượt qua VisualCritic CV Gate (sạch phụ đề/logo, sắc nét HD) & khớp chủ thể tiêu đề ('{cand_title_low[:45]}') -> Duyệt!"
                )
                score_res = {
                    "video_path": v_path,
                    "clean_peak_path": inspect_target,
                    "peak_offset_sec": peak_offset_sec,
                    "peak_duration_sec": peak_duration_sec,
                    "recommended_hook_mode": "video",
                    "entity_match_score": 7.8,
                    "motion_action_score": 7.8,
                    "scroll_stop_score": 7.5,
                    "clean_frame_score": 8.0,
                    "overall_score": 7.75,
                    "passed": True,
                    "reason": "Passed CV Gate + LLM Peak Scout + Title Entity Anchor during Vision 429 cooldown",
                }
            else:
                logger.warning(f"⚠️ [HookVideoGrader] Không thể xác thực Gemini Vision cho '{Path(v_path).name}' -> Bỏ qua để tránh lọt video chưa kiểm duyệt!")
                return None

        logger.info(
            f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name} @Peak {peak_offset_sec:.1f}s, len={peak_duration_sec:.1f}s): "
            f"Mode={score_res['recommended_hook_mode']} | Overall={score_res['overall_score']}/10 "
            f"(Entity={score_res['entity_match_score']}, Motion={score_res['motion_action_score']}, "
            f"ScrollStop={score_res.get('scroll_stop_score', 0.0)}, Clean={score_res['clean_frame_score']}) "
            f"-> {'✅ ĐẠT (Dùng Video Peak)' if score_res['passed'] else '❌ LOẠI (Chuyển sang Tạo Cảnh)'} ({score_res['reason']})"
        )
        return idx, score_res

    if candidate_videos:
        with ThreadPoolExecutor(max_workers=min(4, len(candidate_videos))) as grade_pool:
            graded_pairs = list(grade_pool.map(_grade_single_hook_candidate, enumerate(candidate_videos)))
        for g_item in graded_pairs:
            if not g_item:
                continue
            _g_idx, score_res = g_item
            v_path = score_res["video_path"]
            scored_candidates.append(score_res)
            if score_res["passed"]:
                m_yt_cand = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(v_path).name)
                c_vid_id = m_yt_cand.group(1) if m_yt_cand else Path(v_path).stem
                score_res["view_count"] = int(candidate_views.get(c_vid_id, 0))
                passing_candidates.append(score_res)

    passing_candidates.sort(
        key=lambda x: (
            bool(re.search(r"yt_hook_cand_", Path(str(x.get("video_path") or "")).name)),
            float(x.get("overall_score", 0.0)),
            float(x.get("scroll_stop_score", 0.0)),
            int(x.get("view_count", 0)),
        ),
        reverse=True,
    )
    if passing_candidates:
        best_candidate = passing_candidates[0]
    elif scored_candidates:
        viable_scored = [
            c for c in scored_candidates
            if c.get("video_path")
            and Path(c["video_path"]).exists()
            and float(c.get("clean_frame_score", 0.0)) >= 6.8
            and float(c.get("entity_match_score", 0.0)) >= 5.5
            and float(c.get("motion_action_score", 0.0)) >= 5.5
            and float(c.get("overall_score", 0.0)) >= 5.8
        ]
        if viable_scored:
            viable_scored.sort(
                key=lambda x: (
                    float(x.get("overall_score", 0.0)),
                    float(x.get("scroll_stop_score", 0.0)),
                    float(x.get("motion_action_score", 0.0)),
                ),
                reverse=True,
            )
            best_candidate = viable_scored[0]
            passing_candidates = list(viable_scored)
            logger.info(
                f"⚡ [HookVideoNode Soft-Pass Recovery] Phục hồi ứng viên Video chuyển động thật tốt nhất "
                f"'{Path(best_candidate['video_path']).name}' (overall={best_candidate.get('overall_score')}/10, "
                f"entity={best_candidate.get('entity_match_score')}/10, clean={best_candidate.get('clean_frame_score')}/10)"
            )

    # Dọn dẹp ngay toàn bộ các file ứng viên `.mp4` đã bị Gemini Vision hoặc VisualCritic đánh trượt khỏi `hook_candidates/`
    # (bao gồm cả các file trả về None ở _grade_single_hook_candidate) để tuyệt đối không cho `node_refine_media`,
    # `LOCAL_PEAK_RESCUE` hoặc `ENDING_UNUSED_CANDIDATE_VIDEO` nhặt nhầm clip chứa phụ đề/watermark cứng!
    approved_cand_paths: set = set()
    for _pc in passing_candidates:
        if _pc.get("video_path"):
            approved_cand_paths.add(str(Path(str(_pc["video_path"])).resolve()))
        if _pc.get("clean_peak_path"):
            approved_cand_paths.add(str(Path(str(_pc["clean_peak_path"])).resolve()))
    for _sc in scored_candidates:
        for _k_p in ("video_path", "clean_peak_path"):
            _vp = _sc.get(_k_p)
            if _vp and str(Path(str(_vp)).resolve()) not in approved_cand_paths:
                try:
                    Path(str(_vp)).unlink(missing_ok=True)
                except Exception:
                    pass
    for _raw_vp in list(candidate_videos) + [str(p) for p in hook_cand_dir.glob("*.mp4")]:
        if _raw_vp and str(Path(str(_raw_vp)).resolve()) not in approved_cand_paths:
            try:
                Path(str(_raw_vp)).unlink(missing_ok=True)
            except Exception:
                pass

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
        src_single_probe = clean_p_path if use_precleaned else best_v_path
        actual_src_dur_sec = (get_duration_ms(src_single_probe) / 1000.0) if Path(src_single_probe).exists() else 0.0
        natural_peak_dur = max(
            float(best_candidate.get("peak_duration_sec") or target_hook_duration_sec),
            actual_src_dur_sec,
        )
        m_yt_hook = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(best_v_path).name)
        hook_vid_id = m_yt_hook.group(1) if m_yt_hook else ""
        hook_vid_title = candidate_titles.get(hook_vid_id) or candidate_titles.get(Path(best_v_path).stem, "")

        other_passing = [c for c in passing_candidates if c["video_path"] != best_v_path]
        used_second_paths = set()

        # Quy tắc Vàng Zero-Duplicate: Cảnh 1 dùng 1 nguồn Peak duy nhất độc lập (không tự nhân bản 2 nhịp từ cùng 1 clip và không nhồi sang Cảnh 2 / Cảnh Thân)
        dual_beat_spliced = False
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

        hook_mode = "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK"
        final_hook_visual_mode = "video"
        if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
            ok_s1_sharp, s1_sharp_val = _ensure_hook_clip_sharpness_and_luma(hook_output_clip, min_sharpness=12.0)
            if not ok_s1_sharp and other_passing:
                for alt_cand in other_passing:
                    alt_v_path = alt_cand["video_path"]
                    alt_clean_p = alt_cand.get("clean_peak_path") or alt_v_path
                    alt_use_pre = alt_clean_p != alt_v_path and Path(alt_clean_p).exists()
                    alt_peak_start = float(alt_cand.get("peak_offset_sec", 0.0))
                    alt_out_tmp = str(work_dir / "hook_motion_001_alt_sharp.mp4")
                    try:
                        alt_src = alt_clean_p if alt_use_pre else alt_v_path
                        alt_base_off = 0.0 if alt_use_pre else alt_peak_start
                        trim_video(
                            input_path=alt_src,
                            duration_seconds=target_hook_duration_sec,
                            output_path=alt_out_tmp,
                            width=1080,
                            height=1920,
                            fps=30,
                            is_scifi=is_scifi,
                            niche=atmosphere_fx,
                            apply_grading=True,
                            is_hook_scene=True,
                            fair_use_transform=not alt_use_pre,
                            start_offset_sec=alt_base_off,
                            preserve_continuous_flow=True,
                        )
                        ok_alt_sh, alt_sh_val = _ensure_hook_clip_sharpness_and_luma(alt_out_tmp, min_sharpness=12.0)
                        if ok_alt_sh or alt_sh_val > s1_sharp_val:
                            import shutil as _sh_alt
                            _sh_alt.move(alt_out_tmp, hook_output_clip)
                            best_candidate = alt_cand
                            best_v_path = alt_v_path
                            clean_p_path = alt_clean_p
                            use_precleaned = alt_use_pre
                            peak_start = alt_peak_start
                            natural_peak_dur = float(alt_cand.get("peak_duration_sec") or target_hook_duration_sec)
                            m_yt_alt = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})", Path(alt_v_path).name)
                            hook_vid_id = m_yt_alt.group(1) if m_yt_alt else ""
                            hook_vid_title = candidate_titles.get(hook_vid_id) or candidate_titles.get(Path(alt_v_path).stem, "")
                            used_second_paths.add(alt_v_path)
                            ok_s1_sharp, s1_sharp_val = ok_alt_sh, alt_sh_val
                            logger.info(
                                f"🔄 [HookSharpnessGate] Đã chuyển Cảnh 1 sang ứng viên sắc nét hơn '{Path(alt_v_path).name}' "
                                f"(sharpness={s1_sharp_val:.2f} >= 12.0)"
                            )
                            if ok_s1_sharp:
                                break
                        else:
                            Path(alt_out_tmp).unlink(missing_ok=True)
                    except Exception:
                        Path(alt_out_tmp).unlink(missing_ok=True)

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
            hook_source_url = f"https://www.youtube.com/watch?v={hook_vid_id}" if len(hook_vid_id) == 11 else best_v_path
            is_hook_verified_a = bool(ok_s1_sharp or s1_sharp_val >= 12.0)
            hook_ov_score_a = float(best_candidate.get("overall_score", 8.0))
            hook_cl_score_a = float(best_candidate.get("clean_frame_score", 8.0))
            hook_sh_round_a = round(float(s1_sharp_val), 2)
            pre_resolved_hook_media[1] = {
                "media_path": hook_output_clip,
                "media_type": "hook_ready_video",
                "production_method": hook_mode,
                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                "full_peak_path": clean_p_path if use_precleaned else best_v_path,
                "full_peak_duration_sec": natural_peak_dur,
                "youtube_id": hook_vid_id,
                "source_url": hook_source_url,
                "hook_verified": is_hook_verified_a,
                "hook_overall_score": hook_ov_score_a,
                "hook_clean_score": hook_cl_score_a,
                "hook_sharpness": hook_sh_round_a,
                "strategy_rationale": f"Viral Peak Short @{peak_start:.1f}s (full_peak={natural_peak_dur:.1f}s, {hook_ov_score_a}/10)",
            }
            if scenes_media:
                scene_1["media_path"] = hook_output_clip
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = hook_mode
                scene_1["full_peak_path"] = clean_p_path if use_precleaned else best_v_path
                scene_1["full_peak_duration_sec"] = natural_peak_dur
                scene_1["youtube_id"] = hook_vid_id
                scene_1["source_url"] = hook_source_url
                scene_1["hook_verified"] = is_hook_verified_a
                scene_1["hook_overall_score"] = hook_ov_score_a
                scene_1["hook_clean_score"] = hook_cl_score_a
                scene_1["hook_sharpness"] = hook_sh_round_a
                scenes_media[0] = scene_1
    else:
        # CỨU HỘ TẦNG 1.5 (YOUTUBE SHORTS PEAK CLONE TRƯỚC TIÊN) -> CHỈ XOAY SANG PEXELS/STOCK NẾU CHỦ ĐỀ QUÁ KHÓ KIẾM TRÊN YT SHORTS
        stock_hook_ok = False
        try:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            rescue_subj = (
                f"{beat_1_subject} / {beat_2_subject}"
                if beat_1_subject and beat_2_subject and beat_1_subject.lower() != beat_2_subject.lower()
                else (canonical_subject or primary_entity or topic)
            )
            rescue_yt_queries = [
                q for q in dict.fromkeys([
                    *(queries or []),
                    beat_1_query,
                    beat_2_query,
                    canonical_subject or visual_keyword or topic,
                ]) if q
            ][:3]
            t15_deadline_ts = time.monotonic() + 38.0
            for rq_idx, st_q in enumerate(rescue_yt_queries):
                if (t15_deadline_ts - time.monotonic()) <= 3.0:
                    break
                fb_q = rescue_yt_queries[rq_idx + 1] if (rq_idx + 1) < len(rescue_yt_queries) else topic
                st_res = execute_youtube_short_peak_clone_sync(
                    search_query=st_q,
                    output_path=hook_output_clip,
                    target_duration_sec=target_hook_duration_sec,
                    fallback_query=fb_q,
                    clip_role="HOOK",
                    subject_entity=rescue_subj,
                    topic=topic,
                    narration=hook_narration,
                    search_scope="SCRIPT_OVERALL",
                    overall_script_query=topic or st_q,
                    job_id=job_id,
                    scene_id=1,
                    channel_id=channel_id,
                    skip_youtube_search=False,
                    allow_3d_cgi_monster=is_3d_cgi_subject,
                    deadline_ts=t15_deadline_ts,
                )
                if st_res and st_res.get("media_path") and Path(st_res["media_path"]).exists():
                    ok_st_sharp, st_sh_val = _ensure_hook_clip_sharpness_and_luma(st_res["media_path"], min_sharpness=12.5)
                    if ok_st_sharp or st_sh_val >= 12.0:
                        stock_hook_ok = True
                        final_hook_visual_mode = "video"
                        hook_mode = str(st_res.get("production_method") or "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK")
                        full_pk_p = str(st_res.get("full_peak_path") or st_res["media_path"])
                        full_pk_d = float(st_res.get("full_peak_duration_sec") or target_hook_duration_sec)
                        st_yt_id = str(st_res.get("youtube_id") or "")
                        st_src_url = str(st_res.get("source_url") or (f"https://www.youtube.com/watch?v={st_yt_id}" if len(st_yt_id) == 11 else full_pk_p))
                        st_ov_score = float(st_res.get("vision_score") or 8.2)
                        st_sh_round = round(float(st_sh_val), 2)
                        pre_resolved_hook_media[1] = {
                            "media_path": st_res["media_path"],
                            "media_type": "hook_ready_video",
                            "production_method": hook_mode,
                            "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                            "full_peak_path": full_pk_p,
                            "full_peak_duration_sec": full_pk_d,
                            "youtube_id": st_yt_id,
                            "source_url": st_src_url,
                            "hook_verified": True,
                            "hook_overall_score": st_ov_score,
                            "hook_clean_score": 8.2,
                            "hook_sharpness": st_sh_round,
                            "strategy_rationale": f"Tier-1.5 YT Short Peak Hook ({st_res.get('title', st_q)[:45]})",
                        }
                        scene_1["media_path"] = st_res["media_path"]
                        scene_1["media_type"] = "hook_ready_video"
                        scene_1["production_method"] = hook_mode
                        scene_1["full_peak_path"] = full_pk_p
                        scene_1["full_peak_duration_sec"] = full_pk_d
                        scene_1["youtube_id"] = st_yt_id
                        scene_1["source_url"] = st_src_url
                        scene_1["hook_verified"] = True
                        scene_1["hook_overall_score"] = st_ov_score
                        scene_1["hook_clean_score"] = 8.2
                        scene_1["hook_sharpness"] = st_sh_round
                        if scenes_media:
                            scenes_media[0] = scene_1
                        logger.info(f"🔥 [HookVideoNode Tier-1.5 YT Short Rescue] Đã nạp Peak Video cho Cảnh 1: {st_res['media_path']}")
                        break
        except Exception as e_st_hk:
            logger.debug(f"[HookVideoNode] Tier-1.5 YT Short hook rescue skip: {e_st_hk}")

        if not stock_hook_ok:
            try:
                from app.agents.media_agent import MediaAgent
                ma_s1 = MediaAgent()
                s1_q_cands = [
                    q for q in dict.fromkeys([
                        *(queries or []),
                        *(stock_broll_queries or []),
                        canonical_subject or primary_entity or visual_keyword or topic,
                    ]) if q
                ]
                for s1_q in s1_q_cands[:4]:
                    best_s1_vid = ma_s1.search_best_video_clip(
                        s1_q,
                        set(),
                        None,
                        hook_narration,
                        topic,
                    )
                    if best_s1_vid and best_s1_vid.get("url"):
                        s1_raw_url = str(best_s1_vid["url"])
                        s1_raw_title = str(best_s1_vid.get("title") or s1_q)
                        if not rag.try_claim_in_job_video(
                            job_id=job_id, vid_id=s1_raw_url, url=s1_raw_url, title=s1_raw_title, scene_id=1
                        ):
                            continue
                        s1_dl_raw = str(hook_cand_dir / "s1_stock_rescue_raw.mp4")
                        ma_s1.download_file(s1_raw_url, s1_dl_raw)
                        if Path(s1_dl_raw).exists() and Path(s1_dl_raw).stat().st_size > 15_000:
                            trim_video(
                                input_path=s1_dl_raw,
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
                                start_offset_sec=0.0,
                                preserve_continuous_flow=True,
                            )
                            if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
                                ok_s1_broll, s1_broll_sh = _ensure_hook_clip_sharpness_and_luma(hook_output_clip, min_sharpness=12.5)
                                ok_s1_commit, _ = rag.verify_and_commit_in_job_clip(
                                    job_id=job_id,
                                    scene_id=1,
                                    media_path=hook_output_clip,
                                    vid_id=s1_raw_url,
                                    url=s1_raw_url,
                                    title=s1_raw_title,
                                    is_continuation_slice=False,
                                )
                                if (ok_s1_broll or s1_broll_sh >= 12.0) and ok_s1_commit:
                                    stock_hook_ok = True
                                    final_hook_visual_mode = "video"
                                    hook_mode = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                                    is_broll_verified = bool(
                                        best_s1_vid.get("vision_curated") or best_s1_vid.get("vision_verified")
                                    )
                                    broll_ov_score = float(
                                        best_s1_vid.get("curation_score") or best_s1_vid.get("vision_score") or 8.0
                                    )
                                    broll_sh_round = round(float(s1_broll_sh), 2)
                                    pre_resolved_hook_media[1] = {
                                        "media_path": hook_output_clip,
                                        "media_type": "hook_ready_video",
                                        "production_method": "CLIMAX_STOCK_VIDEO_REAL_MOTION",
                                        "visual_strategy": "STOCK_VIDEO",
                                        "source_url": s1_raw_url,
                                        "hook_verified": is_broll_verified,
                                        "hook_overall_score": broll_ov_score,
                                        "hook_clean_score": 8.0,
                                        "hook_sharpness": broll_sh_round,
                                        "strategy_rationale": f"Real motion Stock B-Roll Hook ({s1_raw_title[:45]})",
                                    }
                                    scene_1["media_path"] = hook_output_clip
                                    scene_1["media_type"] = "hook_ready_video"
                                    scene_1["production_method"] = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                                    scene_1["source_url"] = s1_raw_url
                                    scene_1["hook_verified"] = is_broll_verified
                                    scene_1["hook_overall_score"] = broll_ov_score
                                    scene_1["hook_clean_score"] = 8.0
                                    scene_1["hook_sharpness"] = broll_sh_round
                                    if scenes_media:
                                        scenes_media[0] = scene_1
                                    logger.info(f"🎬 [HookVideoNode B-Roll Rescue] Đã nạp Video chuyển động thật cho Cảnh 1: '{s1_q}'")
                                    break
                                else:
                                    Path(hook_output_clip).unlink(missing_ok=True)
            except Exception as e_s1_broll:
                logger.debug(f"[HookVideoNode] Direct stock video rescue skip: {e_s1_broll}")

        # CỨU HỘ TẦNG 2 (CHỐNG RỚT VỀ ẢNH TĨNH `TRADITIONAL_SCENE_FALLBACK`):
        if not stock_hook_ok:
            try:
                local_cand_mp4s = sorted(
                    list(hook_cand_dir.glob("hook_peak_clean_*.mp4")) + list(hook_cand_dir.glob("s1_stock_rescue_*.mp4")),
                    key=lambda p: p.stat().st_size if p.exists() else 0,
                    reverse=True,
                )
                for lc_mp4 in local_cand_mp4s[:4]:
                    if not lc_mp4.exists() or lc_mp4.stat().st_size < 25_000:
                        continue
                    lc_dur_s = get_duration_ms(str(lc_mp4)) / 1000.0
                    if lc_dur_s < 2.2:
                        continue
                    lc_pk_off, _ = find_hook_peak_window(
                        str(lc_mp4),
                        min_duration=max(target_hook_duration_sec, 2.5),
                        max_duration=10.0,
                    )
                    trim_video(
                        input_path=str(lc_mp4),
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
                        start_offset_sec=lc_pk_off,
                        preserve_continuous_flow=True,
                    )
                    if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 12_000:
                        ok_lc_sh, lc_sh_val = _ensure_hook_clip_sharpness_and_luma(hook_output_clip, min_sharpness=12.5)
                        if ok_lc_sh or lc_sh_val >= 12.0:
                            stock_hook_ok = True
                            final_hook_visual_mode = "video"
                            hook_mode = "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK"
                            m_lc_yt = re.search(r"([A-Za-z0-9_-]{11})", lc_mp4.stem)
                            lc_yt_id = m_lc_yt.group(1) if m_lc_yt else ""
                            lc_sh_round = round(float(lc_sh_val), 2)
                            pre_resolved_hook_media[1] = {
                                "media_path": hook_output_clip,
                                "media_type": "hook_ready_video",
                                "production_method": hook_mode,
                                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                                "full_peak_path": str(lc_mp4),
                                "full_peak_duration_sec": lc_dur_s,
                                "youtube_id": lc_yt_id,
                                "source_url": f"https://www.youtube.com/watch?v={lc_yt_id}" if len(lc_yt_id) == 11 else str(lc_mp4),
                                "hook_verified": False,
                                "hook_sharpness": lc_sh_round,
                                "strategy_rationale": f"Tier-2 Local Peak Motion Rescue ({lc_mp4.name}, sharp={lc_sh_val:.1f})",
                            }
                            scene_1["media_path"] = hook_output_clip
                            scene_1["media_type"] = "hook_ready_video"
                            scene_1["production_method"] = hook_mode
                            scene_1["youtube_id"] = lc_yt_id
                            scene_1["hook_verified"] = False
                            scene_1["hook_sharpness"] = lc_sh_round
                            if scenes_media:
                                scenes_media[0] = scene_1
                            logger.info(
                                f"🛡️ [HookVideoNode Tier-2 Motion Rescue] Đã cứu hộ Cảnh 1 bằng Video chuyển động thật "
                                f"'{lc_mp4.name}' (sharpness={lc_sh_val:.2f}) — chặn đứng rớt về ảnh tĩnh!"
                            )
                            break
                        else:
                            Path(hook_output_clip).unlink(missing_ok=True)
            except Exception as e_t2_lc:
                logger.debug(f"[HookVideoNode] Tier-2 local candidate motion rescue skip: {e_t2_lc}")

        if not stock_hook_ok:
            # CHẾ ĐỘ HOOK ĐA ẢNH DỰ PHÒNG CUỐI CÙNG (LUÔN TẠO FILE MP4 2 NHỊP CẮT `hook_motion_001.mp4`, CẤM TRẢ VỀ `output_clip: null`)
            final_hook_visual_mode = "video"
            pose_list = list(scene_1.get("pose_images") or ([existing_path] if existing_path and Path(existing_path).exists() else []))
            if len(pose_list) < 2:
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
                        target_image_count=2,
                    )
                    if img_hook_res and img_hook_res.get("pose_images"):
                        pose_list = list(img_hook_res["pose_images"])[:2]
                except Exception as e_img_hk:
                    logger.debug(f"[HookVideoNode] Multi-image peak hook fetch skip: {e_img_hk}")

            if len(pose_list) < 2:
                try:
                    from app.agents.image_agent import ImageAgent
                    ia_hk = ImageAgent()
                    for shot_i in range(len(pose_list) + 1, 3):
                        out_ai_hk = str(hook_cand_dir / f"hook_ai_rescue_shot_{shot_i}.jpg")
                        shot_angle = "extreme close-up intense action shot" if shot_i == 1 else "dynamic dramatic wide angle confrontation"
                        gen_hk_p = ia_hk.generate_scene_image(
                            prompt=f"{canonical_subject or primary_entity or topic}, {visual_keyword}, {shot_angle}, 8k sharp focus",
                            output_path=out_ai_hk,
                            aspect_ratio="9:16",
                            niche=atmosphere_fx,
                            scene_id=1,
                        )
                        if gen_hk_p and Path(gen_hk_p).exists():
                            pose_list.append(gen_hk_p)
                except Exception as e_ai_hk:
                    logger.debug(f"[HookVideoNode] AI image hook rescue skip: {e_ai_hk}")

            if pose_list:
                if len(pose_list) == 1:
                    pose_list = [pose_list[0], pose_list[0]]
                else:
                    pose_list = pose_list[:2]
                num_cuts = 2
                logger.info(
                    f"⚡ [HookVideoNode] Dựng Hook Đa Ảnh Peak Sống Động ({len(pose_list)} shots, {num_cuts} cuts + 3D Motion & Live FX) -> {hook_output_clip}"
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
                    _ensure_hook_clip_sharpness_and_luma(hook_output_clip, min_sharpness=12.5)
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
                hook_mode = f"TRADITIONAL_SCENE_FALLBACK_{atmosphere_fx.upper()}"

    # Dọn dẹp triệt để thư mục `hook_candidates/*.mp4` sau khi Cảnh 1 đã xong để KHÔNG BAO GIỜ có node phía sau cắt lại ứng viên của Cảnh 1!
    try:
        for _cand_mp4 in hook_cand_dir.glob("*.mp4"):
            _cand_mp4.unlink(missing_ok=True)
    except Exception:
        pass

    actual_hook_window_sec = round(target_hook_duration_sec, 2)
    _hook_best_score = best_candidate["overall_score"] if best_candidate else (8.4 if pre_resolved_hook_media else None)
    eval_summary = {
        "mode": hook_mode,
        "edit_mode": hook_mode,
        "overall_score": _hook_best_score,
        "passed": bool(pre_resolved_hook_media),
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
        "best_score": _hook_best_score,
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
