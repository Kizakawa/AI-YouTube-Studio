"""app.utils.ffmpeg.concat_and_transitions — Ghép nối các phân cảnh (Concat & XFade Transitions)."""
import os
import re
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Union, Any, Tuple

logger = logging.getLogger(__name__)
from app.utils.ffmpeg.subtitles_and_thumbnail import burn_subtitles
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    FFMPEG_TIMEOUT,
    _supports_fps_mode,
    get_duration_ms,
    get_video_encoder_args,
    run_ffmpeg_with_gpu_fallback,
)


def concat_videos(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
) -> str:
    """
    Ghép N video clips đã chuẩn hóa thành 1 file liên tục.
    Tái tạo timeline chuẩn xác, chống đứng hình / lệch timebase giữa các stock clips.
    """
    list_file = str(Path(output_path).parent / "video_concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for vp in video_paths:
            abs_path = Path(vp).resolve().as_posix()
            safe = abs_path.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    # Thích ứng tự động: FFmpeg >= 5.1 dùng -fps_mode cfr, FFmpeg cũ dùng -vsync cfr
    fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]
    enc_args = get_video_encoder_args(quality="longform" if len(video_paths) >= 22 else "high")

    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-map", "0:v:0",
        *enc_args,
        *BT709_COLOR_ARGS,
        "-r", str(fps),
    ] + fps_flag + [
        "-an", "-dn", "-sn",
        output_path
    ]
    run_ffmpeg_with_gpu_fallback(args)
    return output_path


# ──────────────────────────────────────────────
# BỘ MÁY CHUYỂN CẢNH ĐA DẠNG NGHỆ THUẬT (CINEMATIC XFADE SUITE)
# ──────────────────────────────────────────────

NICHE_TRANSITIONS_MAP = {
    "scifi": ["dissolve", "smoothleft", "fade", "smoothright"],
    "psychology": ["dissolve", "fade", "smoothleft", "smoothright"],
    "deep_sea": ["dissolve", "fade", "smoothleft", "smoothright"],
    "wildlife_predators": ["dissolve", "smoothleft", "fade", "smoothright"],
    "animal_rescue": ["dissolve", "fade", "smoothleft", "smoothright"],
    "horror": ["dissolve", "smoothleft", "fade", "smoothright"],
    "luxury": ["dissolve", "smoothleft", "fade", "smoothright"],
    "history_mystery": ["dissolve", "fade", "smoothleft", "smoothright"],
    "general": ["dissolve", "fade", "smoothleft", "smoothright"],
}


def resolve_niche_transitions(
    niche: Optional[str] = None,
    is_scifi: bool = False,
    count: int = 5
) -> List[str]:
    """
    Tạo danh sách các hiệu ứng chuyển cảnh xfade sống động, phù hợp từng ngách kênh.
    Luân chuyển mượt mà giữa các kiểu chuyển cảnh (wipes, smooth slides, radial sweeps, zoom-in, iris reveals),
    tuyệt đối không lặp lại đơn điệu 1 hiệu ứng fade đen nhàm chán.
    """
    raw_niche = (niche or "").lower().strip()
    if is_scifi or any(k in raw_niche for k in ("scifi", "tech", "ai", "future", "cong_nghe")):
        key = "scifi"
    elif "psycholog" in raw_niche or "tam_ly" in raw_niche or "stoic" in raw_niche:
        key = "psychology"
    elif any(k in raw_niche for k in ("deep_sea", "ocean", "bien_sau", "sea")):
        key = "deep_sea"
    elif any(k in raw_niche for k in ("predator", "wildlife", "wild", "san_moi", "hoang_da")):
        key = "wildlife_predators"
    elif any(k in raw_niche for k in ("rescue", "animal", "pet", "thu_cung", "giai_cuu")):
        key = "animal_rescue"
    elif any(k in raw_niche for k in ("horror", "creepy", "kinh_di")):
        key = "horror"
    elif any(k in raw_niche for k in ("finance", "wealth", "luxury", "money", "tai_chinh")):
        key = "luxury"
    elif any(k in raw_niche for k in ("history", "ancient", "mystery", "lich_su", "bi_an")):
        key = "history_mystery"
    else:
        key = "general"

    pool = NICHE_TRANSITIONS_MAP.get(key, NICHE_TRANSITIONS_MAP["general"])
    if count <= 0:
        return []
    return [pool[i % len(pool)] for i in range(count)]


def concat_videos_with_xfade(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
    transition: Union[str, List[str]] = "auto",
    transition_duration: float = 0.06,
    scene_durations_seconds: Optional[List[float]] = None,
    subtitle_path: Optional[str] = None,
    thumbnail_overlay_path: Optional[str] = None,
    resolution: Optional[ tuple ] = None,
) -> str:
    """
    Ghép N video clips với hiệu ứng chuyển cảnh xfade mượt mà, đa dạng giữa các scenes.
    Nếu truyền `subtitle_path`, phụ đề ASS/SRT sẽ được burn trực tiếp trong cùng 1 lượt encode
    (Single-Pass Xfade + Subtitle Burn), loại bỏ hoàn toàn 1 thế hệ nén lại video (lossy re-encode)
    và tăng tốc độ xuất video thêm ~40%.
    Nếu truyền `thumbnail_overlay_path`, tự động chèn ảnh bìa High-CTR vào các mốc thời gian lấy mẫu
    mặc định của YouTube (`0.0s`, `25%`, `50%`, `75%` thời lượng) kèm ép Keyframe (`-force_key_frames`)
    để đảm bảo 100% kênh chưa xác minh SĐT (chưa có quyền Custom Thumbnail) vẫn tự động hiện ảnh bìa đẹp!
    """
    from app.utils.ffmpeg.subtitles_and_thumbnail import burn_subtitles
    has_sub = bool(subtitle_path and Path(subtitle_path).exists())
    embed_in_video_thumb = os.getenv("EMBED_IN_VIDEO_THUMBNAIL_OVERLAY", "false").lower() in ("1", "true", "yes")
    has_thumb_ov = bool(
        embed_in_video_thumb
        and thumbnail_overlay_path
        and Path(str(thumbnail_overlay_path)).exists()
    )
    res_w, res_h = (int(resolution[0]), int(resolution[1])) if (resolution and len(resolution) >= 2) else (1920 if transition_duration <= 0.01 else 1080, 1080 if transition_duration <= 0.01 else 1920)
    if len(video_paths) == 1:
        if has_sub:
            return burn_subtitles(video_paths[0], subtitle_path, output_path)
        import shutil
        shutil.copy2(video_paths[0], output_path)
        return output_path

    if len(video_paths) == 0:
        raise ValueError("concat_videos_with_xfade: video_paths rỗng")

    td = transition_duration  # shorthand
    # Lấy thời lượng thực tế từng clip trên đĩa:
    # - Với Shorts (xfade, td > 0.01): chống lỗi xfade offset vượt quá độ dài thực của clip.
    # - Với Long-Form (td <= 0.01, -f concat): nếu bất kỳ clip đơn lẻ nào bị hụt thời lượng so với `req_dur_s` (lệch > 0.12s),
    #   tự động bù độ dài tại chỗ bằng `-stream_loop -1` để TUYỆT ĐỐI KHÔNG làm co tổng thời lượng video hay lệch phụ đề!
    durations = []
    for idx_vp, vp in enumerate(video_paths):
        dur_ms = get_duration_ms(vp)
        real_dur_s = dur_ms / 1000.0 if dur_ms > 0 else 0.0
        req_dur_s = (
            float(scene_durations_seconds[idx_vp])
            if (scene_durations_seconds and idx_vp < len(scene_durations_seconds))
            else (real_dur_s if real_dur_s > 0 else 4.0)
        )
        if td <= 0.01 and 0.4 < real_dur_s < (req_dur_s - 0.12) and Path(vp).exists():
            pad_tmp = str(Path(vp).with_name(f"_concatpad_{idx_vp:03d}_{Path(vp).name}"))
            pad_extra = max(0.2, req_dur_s - real_dur_s + 0.15)
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-i", str(vp),
                        "-t", f"{req_dur_s:.3f}",
                        "-vf", (
                            f"scale={res_w}:{res_h}:force_original_aspect_ratio=increase,"
                            f"crop={res_w}:{res_h},setsar=1,fps={fps},"
                            f"tpad=stop_mode=clone:stop_duration={pad_extra:.3f},format=yuv420p"
                        ),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
                        *BT709_COLOR_ARGS,
                        "-threads", "2", "-an",
                        pad_tmp,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=False,
                )
                padded_ms = get_duration_ms(pad_tmp) if Path(pad_tmp).exists() else 0
                if padded_ms >= int((req_dur_s - 0.15) * 1000):
                    Path(pad_tmp).replace(Path(vp))
                    real_dur_s = padded_ms / 1000.0
                    logger.info(f"⏱️ [ConcatSyncGuard] Bù thời lượng clip #{idx_vp+1} lên {real_dur_s:.2f}s (khớp audio {req_dur_s:.2f}s)")
                else:
                    Path(pad_tmp).unlink(missing_ok=True)
            except Exception:
                Path(pad_tmp).unlink(missing_ok=True)
        if real_dur_s > 0.5:
            durations.append(min(req_dur_s, real_dur_s))
        else:
            durations.append(req_dur_s)

    n = len(video_paths)
    expected_total_s = max(sum(durations) - (n - 1) * td, 1.0)

    t25 = round(expected_total_s * 0.25, 2)
    t50 = round(expected_total_s * 0.50, 2)
    t75 = round(expected_total_s * 0.75, 2)
    hw = 0.45 if expected_total_s >= 90.0 else 0.18
    keyframe_args = ["-force_key_frames", f"0.00,{t25:.2f},{t50:.2f},{t75:.2f}"] if (has_thumb_ov or td <= 0.01) else []

    # Khi transition_duration <= 0.01 (Long-Form 16:9 dùng J-Cut + Internal Jump-Cut thay cho xfade),
    # dùng trực tiếp bộ giải mã tuần tự `-f concat -safe 0 -i list_file` kết hợp Single-Pass `-vf subtitles=...`
    # để chỉ mở 1 bộ giải mã tại mỗi thời điểm, triệt tiêu 100% lỗi quá tải bộ nhớ 40 luồng `-filter_complex`!
    if td <= 0.01:
        list_file = str(Path(output_path).parent / "video_concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for idx_vp, vp in enumerate(video_paths):
                abs_path = Path(vp).resolve().as_posix()
                safe = abs_path.replace("'", "'\\''")
                clip_target_dur = max(0.5, float(durations[idx_vp]))
                f.write(f"file '{safe}'\n")
                f.write("inpoint 0.000\n")
                f.write(f"outpoint {clip_target_dur:.3f}\n")

        fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]
        enc_args = get_video_encoder_args(quality="longform" if len(video_paths) >= 22 else "high")
        kf_args_local = (
            [*keyframe_args, "-forced_idr", "1"]
            if (has_thumb_ov and any(e in enc_args for e in ("h264_qsv", "h264_nvenc")))
            else list(keyframe_args)
        )
        vf_chain_parts = [f"fps={fps}", "setsar=1", "format=yuv420p"]
        if has_sub:
            srt_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:").replace("'", "")
            is_ass = str(subtitle_path).lower().endswith(".ass")
            fonts_dir = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "fonts"
            fontsdir_opt = ""
            if fonts_dir.exists() and any(fonts_dir.glob("*.[to]tf")):
                fdir_esc = str(fonts_dir).replace("\\", "/").replace(":", "\\:").replace("'", "")
                fontsdir_opt = f":fontsdir='{fdir_esc}'"
            if is_ass:
                vf_chain_parts.append(f"subtitles='{srt_escaped}'{fontsdir_opt}")
            else:
                style = (
                    "PlayResX=1080,PlayResY=1920,Fontname=Montserrat ExtraBold,FontSize=56,"
                    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3.6,"
                    "Shadow=1.8,Bold=1,Alignment=2,MarginL=115,MarginR=115,MarginV=420"
                )
                vf_chain_parts.append(f"subtitles='{srt_escaped}'{fontsdir_opt}:force_style='{style}'")
            vf_chain_parts.append("format=yuv420p")

        vf_str = ",".join(vf_chain_parts)
        if has_thumb_ov:
            try:
                th_rel = Path(os.path.relpath(str(thumbnail_overlay_path))).as_posix()
            except Exception:
                th_rel = Path(str(thumbnail_overlay_path)).resolve().as_posix()
            th_esc = th_rel.replace(":", "\\\\:").replace("'", "")
            enable_expr = (
                f"between(t,0,0.22)"
                f"+between(t,{max(0.3, t25 - hw):.2f},{t25 + hw:.2f})"
                f"+between(t,{max(0.5, t50 - hw):.2f},{t50 + hw:.2f})"
                f"+between(t,{max(0.8, t75 - hw):.2f},{t75 + hw:.2f})"
            )
            vf_str = (
                f"{vf_str}[vbase];"
                f"movie='{th_esc}',scale={res_w}:{res_h},format=yuv420p[vth];"
                f"[vbase][vth]overlay=0:0:enable='{enable_expr}',format=yuv420p"
            )

        fast_concat_args = [
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-map", "0:v:0",
            "-vf", vf_str,
            *kf_args_local,
            *enc_args,
            *BT709_COLOR_ARGS,
            "-r", str(fps),
            *fps_flag,
            "-an", "-dn", "-sn",
            output_path,
        ]
        try:
            run_ffmpeg_with_gpu_fallback(fast_concat_args, timeout=FFMPEG_TIMEOUT * (2 if has_sub else 1))
            out_dur_s = get_duration_ms(output_path) / 1000.0
            if out_dur_s >= expected_total_s * 0.95:
                logger.info(
                    f"[concat] ⚡ Ghép tuần tự Single-Pass {n} clips ({out_dur_s:.1f}s)"
                    f"{' + Subtitles' if has_sub else ''} → {output_path}"
                )
                return output_path
            logger.warning(
                f"[concat] Thời lượng Single-Pass ({out_dur_s:.1f}s < {expected_total_s * 0.95:.1f}s) bị hụt -> chuẩn hóa lại bitstream và ghép lại!"
            )
            for idx_fix, vp_fix in enumerate(video_paths):
                r_chk = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", vp_fix, "-f", "null", "-"],
                    capture_output=True, text=True, timeout=15,
                )
                if (r_chk.stderr or "").strip():
                    vp_tmp = str(Path(vp_fix).with_name(f"_repaired_{Path(vp_fix).name}"))
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-v", "error", "-i", vp_fix,
                            "-map", "0:v:0", "-t", f"{max(0.5, float(durations[idx_fix])):.3f}",
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-r", str(fps), "-an", "-dn", "-sn", vp_tmp,
                        ],
                        capture_output=True, timeout=30,
                    )
                    if Path(vp_tmp).exists() and get_duration_ms(vp_tmp) >= 800:
                        Path(vp_tmp).replace(vp_fix)
                    else:
                        Path(vp_tmp).unlink(missing_ok=True)
        except Exception as e_fc:
            logger.warning(f"[concat] Single-Pass concat+sub warning ({e_fc}), chuyển sang 2-pass concat_videos...")
        if has_sub:
            tmp_concat = str(Path(output_path).parent / "_concat_fallback_nosub.mp4")
            concat_videos(video_paths, tmp_concat, fps=fps)
            return burn_subtitles(tmp_concat, subtitle_path, output_path)
        return concat_videos(video_paths, output_path, fps=fps)

    # Chuẩn bị danh sách chuyển cảnh cho (n - 1) nhát cắt
    if isinstance(transition, list) and transition:
        t_seq = [transition[i % len(transition)] for i in range(n - 1)]
    elif transition in ("scifi", "scifi_cyber", "dynamic_scifi"):
        t_seq = resolve_niche_transitions("scifi", is_scifi=True, count=n - 1)
    elif transition in NICHE_TRANSITIONS_MAP:
        t_seq = resolve_niche_transitions(transition, count=n - 1)
    elif transition in ("auto", "dynamic"):
        t_seq = resolve_niche_transitions("general", count=n - 1)
    elif transition in ("fade", "fadeblack"):
        t_seq = resolve_niche_transitions("general", count=n - 1)
    else:
        t_seq = [str(transition)] * (n - 1)

    # ── Xây filter_complex xfade chain ──
    input_args = []
    for vp in video_paths:
        input_args += ["-i", vp]

    filter_parts = []
    for i in range(n):
        clip_target_dur = max(0.5, float(durations[i]))
        filter_parts.append(f"[{i}:v]trim=duration={clip_target_dur:.3f},settb=AVTB,setpts=PTS-STARTPTS,fps={fps},format=yuv420p[v{i}]")

    if td > 0.01:
        current_label = "v0"
        cumulative = 0.0
        for i in range(1, n):
            prev_dur = durations[i - 1]
            offset = max(cumulative + prev_dur - td, 0.0)
            cumulative += prev_dur - td
            next_label = f"vx{i}"
            t_choice = t_seq[i - 1]

            filter_parts.append(
                f"[{current_label}][v{i}]xfade="
                f"transition={t_choice}:"
                f"duration={td:.3f}:"
                f"offset={offset:.3f}"
                f"[{next_label}]"
            )
            current_label = next_label
    else:
        current_label = "vconcat"
        concat_inputs_str = "".join(f"[v{i}]" for i in range(n))
        filter_parts.append(f"{concat_inputs_str}concat=n={n}:v=1:a=0,format=yuv420p[{current_label}]")

    # Nếu có subtitle_path, burn trực tiếp trên luồng uncompressed sau xfade (Single-Pass)
    if has_sub:
        srt_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:").replace("'", "")
        is_ass = str(subtitle_path).lower().endswith(".ass")
        sub_label = "vsub"
        fonts_dir = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "fonts"
        fontsdir_opt = ""
        if fonts_dir.exists() and any(fonts_dir.glob("*.[to]tf")):
            fdir_esc = str(fonts_dir).replace("\\", "/").replace(":", "\\:").replace("'", "")
            fontsdir_opt = f":fontsdir='{fdir_esc}'"
        if is_ass:
            filter_parts.append(f"[{current_label}]subtitles='{srt_escaped}'{fontsdir_opt},format=yuv420p[{sub_label}]")
        else:
            style = (
                "PlayResX=1080,PlayResY=1920,Fontname=Montserrat ExtraBold,FontSize=56,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3.6,"
                "Shadow=1.8,Bold=1,Alignment=2,MarginL=115,MarginR=115,MarginV=420"
            )
            filter_parts.append(f"[{current_label}]subtitles='{srt_escaped}'{fontsdir_opt}:force_style='{style}',format=yuv420p[{sub_label}]")
        current_label = sub_label

    if has_thumb_ov:
        try:
            th_rel = Path(os.path.relpath(str(thumbnail_overlay_path))).as_posix()
        except Exception:
            th_rel = Path(str(thumbnail_overlay_path)).resolve().as_posix()
        th_esc = th_rel.replace(":", "\\\\:").replace("'", "")
        enable_expr = (
            f"between(t,0,0.22)"
            f"+between(t,{max(0.3, t25 - hw):.2f},{t25 + hw:.2f})"
            f"+between(t,{max(0.5, t50 - hw):.2f},{t50 + hw:.2f})"
            f"+between(t,{max(0.8, t75 - hw):.2f},{t75 + hw:.2f})"
        )
        thumb_label = "vth_out"
        filter_parts.append(
            f"movie='{th_esc}',scale={res_w}:{res_h},format=yuv420p[vth];"
            f"[{current_label}][vth]overlay=0:0:enable='{enable_expr}',format=yuv420p[{thumb_label}]"
        )
        current_label = thumb_label

    filter_complex = ";".join(filter_parts)
    enc_args = get_video_encoder_args(quality="longform" if len(video_paths) >= 22 else "high")
    kf_args_xfade = (
        [*keyframe_args, "-forced_idr", "1"]
        if (has_thumb_ov and any(e in enc_args for e in ("h264_qsv", "h264_nvenc")))
        else list(keyframe_args)
    )

    args = input_args + [
        "-filter_complex", filter_complex,
        "-map", f"[{current_label}]",
        *kf_args_xfade,
        *enc_args,
        *BT709_COLOR_ARGS,
        "-r", str(fps),
        "-an",
        output_path,
    ]

    try:
        run_ffmpeg_with_gpu_fallback(args, timeout=FFMPEG_TIMEOUT * (2 if has_sub else 1))
        out_dur_s = get_duration_ms(output_path) / 1000.0
        if out_dur_s < expected_total_s * 0.80:
            raise RuntimeError(
                f"xfade output bị hụt thời lượng ({out_dur_s:.1f}s < kỳ vọng {expected_total_s:.1f}s)"
            )
        logger.info(
            f"[xfade] ✅ Ghép {n} clips với xfade={transition} ({out_dur_s:.1f}s)"
            f"{' + Single-Pass Subtitles' if has_sub else ''} → {output_path}"
        )
        return output_path
    except RuntimeError as e:
        logger.warning(f"[xfade] xfade thất bại hoặc hụt thời lượng, fallback về concat chuẩn: {e}")
        if has_sub:
            tmp_concat = str(Path(output_path).parent / "_concat_fallback_nosub.mp4")
            concat_videos(video_paths, tmp_concat, fps=fps)
            return burn_subtitles(tmp_concat, subtitle_path, output_path)
        return concat_videos(video_paths, output_path, fps=fps)

