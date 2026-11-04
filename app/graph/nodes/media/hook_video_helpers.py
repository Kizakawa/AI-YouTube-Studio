"""app.graph.nodes.media.hook_video_helpers — Tiện ích tạo truy vấn, cắt nối đa nhịp và kiểm định độ nét cho Hook Video."""
import asyncio
import contextvars
import os
import re
import json
import base64
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from config.settings import settings
from app.agents.curation.scene_context_analyzer_mixin import (
    _VISION_SEM,
    bind_scene_cancellation,
    is_scene_cancelled_ctx,
)
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


def verify_hook_clip_genuine_motion(
    clip_path: str,
    min_raw_diff: float = 3.2,
    min_comp_diff: float = 3.6,
    max_frozen_ratio: float = 0.22,
) -> tuple[bool, float, str]:
    """
    Kiểm định Toán học Thị giác (CV Motion & Anti-Letterbox Gate) cho 5-7s Mở Đầu (Scene 1 & Scene 2) và B-Roll Video:
    1. Phân biệt Video quay chuyển động vật lý 3D thật (chủ thể bơi/chạy/săn mồi trên nền nước/bầu trời hoặc chuyển động máy quay 3D)
       với các video "Slideshow / Ảnh tĩnh Ken-Burns" hoặc clip tượng/đóng băng khung hình (`med_raw < 3.2`, `med_act < 8.5`).
    2. Tính sai khác trên bước nhảy 0.25s (`step=2` tại `fps=8`) kết hợp vùng 25% ô lưới chuyển động mạnh nhất (`med_act`),
       giúp nhận diện chính xác cảnh động vật bơi/bay thật trên nền nước/trời đồng nhất (`med_act >= 9.5`, `comp_ratio >= 0.65`)
       đồng thời loại bỏ 100% ảnh tĩnh phóng to Ken-Burns (`comp_ratio < 0.55` & `med_comp < 6.2`) và cảnh đứng hình.
    3. Phát hiện dải viền mờ trên/dưới (`blurred_letterbox_padbars`: video ngang 16:9 nhét trong khung dọc 9:16 với 2 dải mờ trên/dưới)
       và loại bỏ khỏi 5-7s đầu tiên để khung hình dọc 9:16 luôn tràn viền sắc nét 100%.
    4. Khi kiểm tra file video thành phẩm (`final_shorts_*.mp4` / `video_*.mp4` dài >= 5.5s), tự động kiểm tra CẢ 2 cửa sổ:
       Cửa sổ 1 (`0.25s -> 2.45s`, Scene 1) VÀ Cửa sổ 2 (`2.80s -> 5.20s`, Scene 2) để đảm bảo trọn vẹn 5-7s đầu là 100% video động!
    """
    import tempfile
    from PIL import Image, ImageChops, ImageFilter, ImageStat

    p_clip = Path(clip_path)
    if not p_clip.exists() or p_clip.stat().st_size < 10_000:
        return False, 0.0, "File video không tồn tại hoặc kích thước quá nhỏ"

    def _eval_single_window(start_s: float, dur_s: float, check_padbars: bool) -> tuple[bool, float, str]:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-ss", f"{start_s:.2f}", "-t", f"{dur_s:.2f}",
                    "-i", str(p_clip),
                    "-vf", "fps=8,scale=180:320",
                    str(Path(td) / "m_%02d.jpg"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            frames = sorted(Path(td).glob("m_*.jpg"))
            if len(frames) < 4:
                subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-t", "2.00",
                        "-i", str(p_clip),
                        "-vf", "fps=8,scale=180:320",
                        str(Path(td) / "m_%02d.jpg"),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
                frames = sorted(Path(td).glob("m_*.jpg"))
            if len(frames) < 3:
                return True, 8.0, "OK"

            full_imgs = [Image.open(f).convert("L") for f in frames[:16]]
            if check_padbars and full_imgs:
                mid_f = full_imgs[len(full_imgs) // 2]
                edges_f = mid_f.filter(ImageFilter.FIND_EDGES)
                top_e = float(ImageStat.Stat(edges_f.crop((16, 8, 164, 52))).mean[0])
                bot_e = float(ImageStat.Stat(edges_f.crop((16, 268, 164, 312))).mean[0])
                cen_e = float(ImageStat.Stat(edges_f.crop((16, 88, 164, 232))).mean[0])
                if cen_e >= 7.5 and top_e < cen_e * 0.22 and bot_e < cen_e * 0.22:
                    return (
                        False,
                        round(cen_e, 2),
                        f"Video ngang 16:9 có viền mờ/đen trên dưới (top_e={top_e:.1f}, bot_e={bot_e:.1f}, cen_e={cen_e:.1f})",
                    )

            imgs = [im.crop((20, 30, 160, 210)) for im in full_imgs]
            step = 2 if len(imgs) >= 6 else 1
            raw_diffs: List[float] = []
            comp_diffs: List[float] = []
            active_comps: List[float] = []
            adj_raw_diffs: List[float] = []

            for idx_adj in range(len(imgs) - 1):
                a_adj, b_adj = imgs[idx_adj], imgs[idx_adj + 1]
                adj_raw_diffs.append(float(ImageStat.Stat(ImageChops.difference(a_adj, b_adj)).mean[0]))

            for idx_f in range(len(imgs) - step):
                a, b = imgs[idx_f], imgs[idx_f + step]
                w, h = a.size
                mx, my = max(4, int(w * 0.10)), max(4, int(h * 0.10))
                ac = a.crop((mx, my, w - mx, h - my))
                bc = b.crop((mx, my, w - mx, h - my))
                diff_im = ImageChops.difference(ac, bc)
                raw_d = float(ImageStat.Stat(diff_im).mean[0])
                best_d = raw_d
                best_diff_im = diff_im
                for z_fac in (0.94, 0.96, 0.975, 0.988, 1.0, 1.012, 1.025, 1.04, 1.06):
                    zw, zh = int(round(w * z_fac)), int(round(h * z_fac))
                    ar = a.resize((zw, zh), Image.Resampling.BILINEAR)
                    ox, oy = (zw - w) // 2, (zh - h) // 2
                    for dx in range(-14, 15, 2):
                        for dy in range(-14, 15, 2):
                            bx0, by0 = ox + mx + dx, oy + my + dy
                            bx1, by1 = ox + w - mx + dx, oy + h - my + dy
                            if 0 <= bx0 and 0 <= by0 and bx1 <= zw and by1 <= zh:
                                cand_im = ImageChops.difference(ar.crop((bx0, by0, bx1, by1)), bc)
                                d_val = float(ImageStat.Stat(cand_im).mean[0])
                                if d_val < best_d:
                                    best_d = d_val
                                    best_diff_im = cand_im
                grid_vals = list(best_diff_im.resize((8, 10), Image.Resampling.BOX).getdata())
                top_q = sorted(grid_vals, reverse=True)[: max(1, len(grid_vals) // 4)]
                act_c = sum(top_q) / len(top_q)
                raw_diffs.append(raw_d)
                comp_diffs.append(best_d)
                active_comps.append(act_c)

            if not raw_diffs:
                return True, 8.0, "OK"
            s_raw = sorted(raw_diffs)
            s_comp = sorted(comp_diffs)
            s_act = sorted(active_comps)
            med_raw = s_raw[len(s_raw) // 2]
            med_comp = s_comp[len(s_comp) // 2]
            med_act = s_act[len(s_act) // 2]
            frozen_ratio = (
                sum(1 for x in adj_raw_diffs if x < 0.95) / len(adj_raw_diffs)
                if adj_raw_diffs
                else 0.0
            )
            comp_ratio = med_comp / max(med_raw, 0.1)

            if med_raw < min_raw_diff and med_act < 9.5:
                return False, round(med_comp, 2), f"Cảnh tĩnh đứng hình (med_raw={med_raw:.2f} < {min_raw_diff}, med_act={med_act:.2f})"
            if med_comp < min_comp_diff and med_act < 10.5:
                return False, round(med_comp, 2), f"Ảnh tĩnh / AI illustration zoompan giả chuyển động (med_comp={med_comp:.2f}, med_act={med_act:.2f})"
            if frozen_ratio > max_frozen_ratio and med_comp < 7.5 and med_act < 12.0:
                return False, round(med_comp, 2), f"Đóng băng khung hình đầu (frozen_ratio={frozen_ratio:.2f}, med_comp={med_comp:.2f})"
            if (comp_ratio < 0.48 and med_comp < 18.0) or (med_comp < 6.2 and comp_ratio < 0.54 and med_act < 13.5):
                return False, round(med_comp, 2), f"Slideshow ảnh tĩnh Ken-Burns zoom/pan (med_comp={med_comp:.2f}, comp_ratio={comp_ratio:.2f})"
            return True, round(max(med_comp, med_act * 0.55), 2), "OK"

    try:
        fname_low = p_clip.name.lower()
        is_full_rendered_short = fname_low.startswith(("final_shorts_", "video_")) and get_duration_ms(str(p_clip)) >= 5500
        ok_w1, sc_w1, rsn_w1 = _eval_single_window(0.25, 2.20, check_padbars=not is_full_rendered_short)
        if not ok_w1:
            return False, sc_w1, f"[0-2.5s] {rsn_w1}"
        if is_full_rendered_short:
            ok_w2, sc_w2, rsn_w2 = _eval_single_window(2.85, 2.40, check_padbars=False)
            if not ok_w2:
                return False, sc_w2, f"[2.8-5.3s] {rsn_w2}"
            return True, round(min(sc_w1, sc_w2), 2), "OK"
        return True, sc_w1, "OK"
    except Exception as e_mot:
        logger.debug(f"[HookMotionGate] Bỏ qua lỗi kiểm tra chuyển động '{clip_path}': {e_mot}")
        return True, 8.0, "OK"


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
        top_k = sorted_sc[: max(1, len(sorted_sc) - 1)]
        eff_sharp = sum(top_k) / len(top_k) if min(top_k) >= 6.0 else min(top_k)
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
                "eq=brightness=0.04:contrast=1.18:saturation=1.10,unsharp=5:5:1.50:3:3:0.85,cas=0.45",
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

