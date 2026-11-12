"""app.utils.ffmpeg.color_and_motion — Chỉnh màu điện ảnh theo ngách (Color Grading), Ken Burns & Multi-Cut Hook."""
import os
import re
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Union, Any, Tuple

logger = logging.getLogger(__name__)
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    VIDEO_DURATION_SAFETY_MARGIN,
    _supports_fps_mode,
    get_video_encoder_args,
    run_ffmpeg_with_gpu_fallback,
)


def get_niche_color_grading(
    niche: Optional[str] = None,
    is_scifi: bool = False,
    is_hook_scene: bool = False,
) -> Dict[str, str]:
    """
    Trả về bộ lọc màu sắc, độ tương phản và vignette điện ảnh chuẩn hóa theo từng ngách (Niche Identity).
    Sử dụng 100% các filter native của FFmpeg (eq, colorbalance, unsharp, vignette) với tốc độ render cao,
    đồng bộ hóa màu sắc cho cả ảnh tĩnh (Ken Burns) và stock video.
    Đặc biệt: Khi `is_hook_scene=True` (3 giây đầu), triệt tiêu mọi vignette tối góc và giữ brightness >= +0.02
    để khung hình 0.0s đạt độ bừng sáng (First-Frame Visual Pop) tối đa trên màn hình điện thoại.
    """
    raw_niche = (niche or "").lower().strip()

    if any(k in raw_niche for k in ("satirical", "comic", "cham_biem", "hoat_hinh_tam_ly")):
        niche_key = "comic_satire"
    elif any(k in raw_niche for k in ("psycholog", "tam_ly", "mind_control", "dark_psych", "manipulation")):
        niche_key = "psychology"
    elif any(k in raw_niche for k in ("deep_sea", "ocean", "bien_sau", "sea")):
        niche_key = "deep_sea"
    elif any(k in raw_niche for k in ("predator", "wildlife", "wild", "san_moi", "hoang_da")):
        niche_key = "wildlife_predators"
    elif any(k in raw_niche for k in ("rescue", "animal", "pet", "thu_cung", "giai_cuu")):
        niche_key = "animal_rescue"
    elif any(k in raw_niche for k in ("history", "ancient", "mystery", "lich_su", "bi_an")):
        niche_key = "history_mystery"
    elif any(k in raw_niche for k in ("horror", "creepy", "kinh_di")):
        niche_key = "horror"
    elif is_scifi or any(k in raw_niche for k in ("scifi", "tech", "ai", "future", "cong_nghe")):
        niche_key = "scifi"
    elif any(k in raw_niche for k in ("finance", "wealth", "luxury", "money", "tai_chinh")):
        niche_key = "luxury"
    elif any(k in raw_niche for k in ("stoic", "philosophy", "triet_hoc")):
        niche_key = "stoic"
    elif any(k in raw_niche for k in ("health", "biohack", "suc_khoe")):
        niche_key = "health"
    else:
        niche_key = "general"

    PROFILES = {
        "comic_satire": {
            "vf_filter": "eq=contrast=1.08:saturation=1.05:brightness=0.01,unsharp=3:3:0.6:3:3:0.25",
            "vignette": "",
        },
        "psychology": {
            "vf_filter": "eq=contrast=1.18:saturation=0.72:brightness=0.02,colorbalance=rs=-0.01:gs=0.00:bs=0.03,unsharp=5:5:0.70:3:3:0.25",
            "vignette": "vignette=0.22",
        },
        "deep_sea": {
            "vf_filter": "eq=contrast=1.16:saturation=1.18:brightness=0.02,colorbalance=rs=-0.03:gs=0.01:bs=0.06,unsharp=3:3:0.65:3:3:0.22",
            "vignette": "vignette=0.24",
        },
        "wildlife_predators": {
            "vf_filter": "eq=contrast=1.08:saturation=1.22:brightness=0.02,unsharp=3:3:0.55:3:3:0.20",
            "vignette": "",
        },
        "animal_rescue": {
            "vf_filter": "eq=contrast=1.04:saturation=1.15:brightness=0.03,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "history_mystery": {
            "vf_filter": "eq=contrast=1.10:saturation=1.14:brightness=0.02,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.55:3:3:0.20",
            "vignette": "vignette=0.20",
        },
        "horror": {
            "vf_filter": "eq=contrast=1.18:saturation=0.88:brightness=0.01,colorbalance=rs=-0.02:gs=0.00:bs=0.04,unsharp=3:3:0.70:3:3:0.25",
            "vignette": "vignette=0.28",
        },
        "scifi": {
            "vf_filter": "eq=contrast=1.08:saturation=1.22:brightness=0.02,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "vignette=0.20",
        },
        "luxury": {
            "vf_filter": "eq=contrast=1.05:saturation=1.16:brightness=0.02,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "stoic": {
            "vf_filter": "eq=contrast=1.16:saturation=0.58:brightness=0.01,colorbalance=rs=-0.01:gs=0.00:bs=0.02,unsharp=5:5:0.65:3:3:0.20",
            "vignette": "vignette=0.22",
        },
        "health": {
            "vf_filter": "eq=contrast=1.04:saturation=1.20:brightness=0.03,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "general": {
            "vf_filter": "eq=contrast=1.06:saturation=1.14:brightness=0.01,unsharp=3:3:0.45:3:3:0.15",
            "vignette": "",
        },
    }

    cfg = PROFILES.get(niche_key, PROFILES["general"])
    vf_filter = cfg["vf_filter"]
    vignette = "" if is_hook_scene else cfg.get("vignette", "")
    full_filter = f"{vf_filter},{vignette}" if vignette else vf_filter

    return {
        "niche_key": niche_key,
        "vf_filter": vf_filter,
        "vignette": vignette,
        "full_filter": full_filter,
    }


# ──────────────────────────────────────────────
# CHECKPOINT A: Ảnh tĩnh → Video (Ken Burns 2.5x Supersampled)
# ──────────────────────────────────────────────

def image_to_video(
    image_path: str,
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    zoom_effect: bool = True,
    camera_motion: str = "zoom_in",
    is_hook_scene: bool = False,
    is_scifi: bool = False,
    niche: Optional[str] = None,
) -> str:
    """
    Biến 1 ảnh tĩnh → video MP4 với Ken Burns 2.5x Supersampled mượt tuyệt đối (khử 100% rung bậc thang sub-pixel)
    kèm Niche Color Grading và đầy đủ chuyển động camera điện ảnh:
      - zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, hyper_zoom, warp_zoom, cinematic_drift.
    """
    import uuid as _uuid
    _uid = _uuid.uuid4().hex[:8]
    norm_path = str(Path(output_path).parent / f"_norm_{_uid}.jpg")
    enable_zoompan = bool(
        zoom_effect
        and os.getenv("ENABLE_KEN_BURNS_ZOOM", "true").strip().lower() not in ("0", "false", "no", "off")
    )
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            if enable_zoompan:
                ss_factor = 2.5 if max(width, height) <= 1920 else 2.0
                scale_w = (int(round(width * ss_factor)) // 2) * 2
                scale_h = (int(round(height * ss_factor)) // 2) * 2
                max_up = 3.8 * ss_factor
            elif zoom_effect:
                scale_w = width + 36
                scale_h = height + 36
                max_up = 4.0
            else:
                scale_w = width
                scale_h = height
                max_up = 3.8
            from app.services.media.image_quality import prepare_vertical
            im_resized = prepare_vertical(
                im, scale_w, scale_h, max_upscale=max_up, is_hook_scene=is_hook_scene
            )
            im_resized.save(norm_path, "JPEG", quality=95 if is_hook_scene else 92)
            image_path = norm_path
    except Exception as e:
        raise ValueError(f"File ảnh không hợp lệ hoặc không thể đọc '{image_path}': {e}")

    d = max(int(round(duration_seconds * fps)) + 2, fps)
    z_start = 1.02 if is_hook_scene else 1.00
    z_end = 1.18 if is_hook_scene else 1.10
    motion_key = (camera_motion or "zoom_in").strip().lower()

    if not enable_zoompan:
        if zoom_effect:
            dur_f = max(float(duration_seconds), 0.8)
            if motion_key in ("pan_left", "trucking_left", "whip_pan"):
                x_expr = f"36-36*min(t/{dur_f:.2f},1)"
                y_expr = "18+10*sin(1.8*t)"
            elif motion_key in ("pan_right", "trucking_right", "tracking_follow"):
                x_expr = f"36*min(t/{dur_f:.2f},1)"
                y_expr = "18-10*sin(1.8*t)"
            elif motion_key in ("pan_up", "pedestal_up", "tilt_up", "worms_eye", "low_angle"):
                x_expr = "18+10*sin(1.8*t)"
                y_expr = f"36-36*min(t/{dur_f:.2f},1)"
            elif motion_key in ("pan_down", "pedestal_down", "tilt_down", "overhead_90", "high_angle"):
                x_expr = "18-10*sin(1.8*t)"
                y_expr = f"36*min(t/{dur_f:.2f},1)"
            elif motion_key in ("zoom_out", "dolly_out"):
                x_expr = "18+14*cos(1.6*t)"
                y_expr = f"36*min(t/{dur_f:.2f},1)"
            elif motion_key in ("arc_orbit", "dutch_angle", "dolly_zoom"):
                x_expr = "18+16*sin(2.0*t)"
                y_expr = "18+12*cos(2.0*t)"
            else:
                x_expr = "18+14*sin(1.6*t)"
                y_expr = f"36-36*min(t/{dur_f:.2f},1)"
            zoompan = (
                f"scale={width+36}:{height+36}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:'{x_expr}':'{y_expr}',"
                f"setsar=1,"
                f"fps={fps},format=yuv420p"
            )
        else:
            zoompan = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,"
                f"fps={fps},format=yuv420p"
            )
    elif motion_key in ("zoom_out", "dolly_out"):
        z_hi = 1.14 if is_hook_scene else 1.12
        zoompan = (
            f"zoompan=z='{z_hi:.2f}-({z_hi - 1.01:.2f})*(on/{d})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("pan_left", "trucking_left", "whip_pan"):
        zoompan = (
            f"zoompan=z='1.12+0.03*(on/{d})':"
            f"x='(iw-iw/zoom)*(1-on/{d})':y='(ih-ih/zoom)/2':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("pan_right", "trucking_right", "tracking_follow"):
        zoompan = (
            f"zoompan=z='1.12+0.03*(on/{d})':"
            f"x='(iw-iw/zoom)*(on/{d})':y='(ih-ih/zoom)/2':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("pan_up", "pedestal_up", "tilt_up", "worms_eye", "low_angle"):
        zoompan = (
            f"zoompan=z='1.12+0.03*(on/{d})':"
            f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*(1-on/{d})':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("pan_down", "pedestal_down", "tilt_down", "overhead_90", "high_angle"):
        zoompan = (
            f"zoompan=z='1.12+0.03*(on/{d})':"
            f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*(on/{d})':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("hyper_zoom", "warp_zoom"):
        zoompan = (
            f"zoompan=z='1.00+0.24*(on/{d})*(on/{d})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    elif motion_key in ("cinematic_drift", "arc_orbit", "dutch_angle", "dolly_zoom"):
        zoompan = (
            f"zoompan=z='1.06+0.08*(on/{d})':"
            f"x='(iw-iw/zoom)*(0.25+0.50*(on/{d}))':y='(ih-ih/zoom)*(0.35+0.30*(on/{d}))':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )
    else:
        zoompan = (
            f"zoompan=z='{z_start:.2f}+({z_end - z_start:.2f})*(on/{d})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},setsar=1"
        )

    grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi, is_hook_scene=is_hook_scene)
    clean_vf_filter = re.sub(r",?unsharp=[^,]+", "", grading["vf_filter"]).strip(",")
    if enable_zoompan:
        vf_parts = [clean_vf_filter] if clean_vf_filter else []
        vf_parts.append(zoompan)
        if grading.get("vignette"):
            vf_parts.append(grading["vignette"])
    else:
        vf_parts = [zoompan]
        if clean_vf_filter:
            vf_parts.append(clean_vf_filter)
        if grading.get("vignette"):
            vf_parts.append(grading["vignette"])

    vf_chain = ",".join(vf_parts) + ",format=yuv420p"

    enc_args = get_video_encoder_args(quality="intermediate")
    fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]
    loop_args = ["-loop", "1"] if not enable_zoompan else []
    args = [
        *loop_args,
        "-i", image_path,
        "-vf", vf_chain,
        "-t", str(duration_seconds),
        *enc_args,
        *BT709_COLOR_ARGS,
        "-r", str(fps),
        *fps_flag,
        "-an",
        output_path,
    ]
    try:
        run_ffmpeg_with_gpu_fallback(args)
    finally:
        try:
            Path(norm_path).unlink(missing_ok=True)
        except Exception:
            pass
    return output_path


def render_opening_hook_multi_cut(
    image_paths: List[str],
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    min_cuts: int = 3,
    is_scifi: bool = False,
    niche: Optional[str] = None,
    is_hook_scene: bool = True,
) -> str:
    """
    Render phân cảnh đa khung hình (Multi-Image Cut) với Supersampled Zoompan 2.2x trên từng sub-shot
    (loại bỏ hoàn toàn lỗi giật 2px của crop động trên yuv420p và không giải mã lại JPEG 30 lần/giây).
    """
    import uuid as _uuid
    from PIL import Image, ImageEnhance
    from app.services.media.image_quality import prepare_vertical

    valid_paths = [
        p for p in (image_paths or [])
        if p and Path(p).exists() and Path(p).stat().st_size > 1000
    ]
    if not valid_paths:
        raise ValueError("render_opening_hook_multi_cut: Không có file ảnh hợp lệ")

    min_dur_for_cut = 2.6 if (is_hook_scene and int(min_cuts or 0) >= 2) else 3.0
    if is_hook_scene and int(min_cuts or 0) >= 2 and duration_seconds >= 2.6:
        max_shots_by_dur = max(1, min(3, max(2, int(duration_seconds // 1.1))))
    else:
        max_shots_by_dur = max(1, min(2, int(duration_seconds // 1.5)))
    usable_valid_paths = valid_paths[:max_shots_by_dur]
    requested_shots = (
        max(len(usable_valid_paths), min(max_shots_by_dur, max(1, int(min_cuts or 0) + 1)))
        if duration_seconds >= min_dur_for_cut
        else 1
    )
    n_shots = requested_shots
    if duration_seconds < min_dur_for_cut or n_shots <= 1:
        return image_to_video(
            image_path=valid_paths[0],
            duration_seconds=duration_seconds,
            output_path=output_path,
            width=width,
            height=height,
            fps=fps,
            zoom_effect=True,
            is_hook_scene=is_hook_scene,
            is_scifi=is_scifi,
            niche=niche,
        )
    valid_paths = usable_valid_paths

    uid = _uuid.uuid4().hex[:8]
    parent_dir = Path(output_path).parent
    temp_files: List[str] = []

    CAMERA_ANGLES = [
        (1.00, 0.50, 0.50, 1.00),
        (1.28, 0.50, 0.34, 1.04),
        (1.38, 0.38, 0.52, 1.06),
        (1.52, 0.56, 0.40, 1.08),
    ]

    ss_w = (int(round(width * 2.2)) // 2) * 2
    ss_h = (int(round(height * 2.2)) // 2) * 2

    def _crop_camera_angle(base_img: Image.Image, zoom: float, cx_ratio: float, cy_ratio: float, contrast: float) -> Image.Image:
        if zoom <= 1.01:
            return base_img.copy()
        bw, bh = base_img.size
        cw = max(int(round(bw / zoom)), 320)
        ch = max(int(round(bh / zoom)), 568)
        cx = int(round(bw * cx_ratio))
        cy = int(round(bh * cy_ratio))
        left = max(0, min(bw - cw, cx - cw // 2))
        top = max(0, min(bh - ch, cy - ch // 2))
        cropped = base_img.crop((left, top, left + cw, top + ch)).resize((ss_w, ss_h), Image.LANCZOS)
        if abs(contrast - 1.0) > 0.01:
            cropped = ImageEnhance.Contrast(cropped).enhance(contrast)
        return cropped

    try:
        base_images: List[Image.Image] = []
        for p in valid_paths[:n_shots]:
            try:
                with Image.open(p) as im:
                    if im.mode != "RGB":
                        im = im.convert("RGB")
                    norm_im = prepare_vertical(im, ss_w, ss_h, max_upscale=6.0, is_hook_scene=is_hook_scene)
                    base_images.append(norm_im)
            except Exception as ex_im:
                logger.debug(f"[render_opening_hook_multi_cut] Lỗi đọc ảnh '{p}': {ex_im}")

        if not base_images:
            raise ValueError(f"Không thể chuẩn hóa bất kỳ ảnh nào từ {valid_paths}")

        shot_paths: List[str] = []
        for idx in range(n_shots):
            angle = CAMERA_ANGLES[idx % len(CAMERA_ANGLES)]
            if idx < len(base_images):
                src_im = base_images[idx]
                if is_hook_scene and len(base_images) >= n_shots and idx > 0:
                    subtle_zoom = 1.0 + (idx * 0.03)
                    shot_im = _crop_camera_angle(src_im, subtle_zoom, angle[1], angle[2], 1.02)
                else:
                    shot_im = src_im
            else:
                src_im = base_images[idx % len(base_images)]
                shot_im = _crop_camera_angle(src_im, angle[0], angle[1], angle[2], angle[3])

            shot_file = str(parent_dir / f"_mc_shot_{uid}_{idx}.jpg")
            shot_im.save(shot_file, "JPEG", quality=94)
            temp_files.append(shot_file)
            shot_paths.append(shot_file)

        if is_hook_scene and n_shots == 4:
            weights = [0.24, 0.24, 0.25, 0.27]
        elif n_shots == 2:
            weights = [0.49, 0.51]
        elif n_shots == 3:
            weights = [0.33, 0.33, 0.34]
        else:
            weights = [1.0 / n_shots] * n_shots

        shot_durs: List[float] = []
        elapsed = 0.0
        for i in range(n_shots):
            if i < n_shots - 1:
                sd = round(max(0.35, duration_seconds * weights[i]), 3)
                shot_durs.append(sd)
                elapsed += sd
            else:
                shot_durs.append(round(max(0.35, duration_seconds - elapsed), 3))

        grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi, is_hook_scene=is_hook_scene)
        clean_vf_filter = re.sub(r",?unsharp=[^,]+", "", grading["vf_filter"]).strip(",")
        live_fx = get_hook_live_fx_filter(niche=niche, is_scifi=is_scifi, width=width, height=height) if is_hook_scene else ""
        post_filters = [f"scale={width}:{height}", "setsar=1", f"fps={fps}"]
        if clean_vf_filter:
            post_filters.append(clean_vf_filter)
        if live_fx:
            post_filters.append(live_fx)
        if grading.get("vignette"):
            post_filters.append(grading["vignette"])
        post_chain = ",".join(post_filters) + ",format=yuv420p"

        input_args: List[str] = []
        concat_inputs: List[str] = []
        per_shot_chains: List[str] = []

        for i, (sp, sd) in enumerate(zip(shot_paths, shot_durs)):
            input_args.extend(["-i", sp])
            d_shot = max(int(round(float(sd) * fps)) + 2, 8)
            zp_modes = [
                (f"1.04+0.06*(on/{d_shot})", f"(iw-iw/zoom)*(0.75-0.50*(on/{d_shot}))", f"(ih-ih/zoom)*(0.70-0.40*(on/{d_shot}))"),
                (f"1.04+0.06*(on/{d_shot})", f"(iw-iw/zoom)*(0.25+0.50*(on/{d_shot}))", f"(ih-ih/zoom)*(0.30+0.40*(on/{d_shot}))"),
                (f"1.05+0.07*(on/{d_shot})", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(0.70-0.40*(on/{d_shot}))"),
                (f"1.10-0.06*(on/{d_shot})", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
            ]
            z_e, x_e, y_e = zp_modes[i % len(zp_modes)]
            per_shot_chains.append(
                f"[{i}:v]zoompan=z='{z_e}':x='{x_e}':y='{y_e}':d={d_shot}:s={width}x{height}:fps={fps},"
                f"trim=duration={sd:.3f},setpts=PTS-STARTPTS,setsar=1[v{i}]"
            )
            concat_inputs.append(f"[v{i}]")

        filter_complex = f"{';'.join(per_shot_chains)};{''.join(concat_inputs)}concat=n={n_shots}:v=1:a=0,{post_chain}[vout]"

        enc_args = get_video_encoder_args(quality="intermediate")
        fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]
        args = [
            *input_args,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-t", f"{duration_seconds:.3f}",
            *enc_args,
            "-r", str(fps),
            *fps_flag,
            "-an",
            output_path,
        ]
        role_tag = (
            "Hook Multi-Cut + Live FX"
            if is_hook_scene
            else ("Body 2-Shot Focal Punch-In 1.28x" if len(base_images) == 1 and n_shots >= 2 else "Body Multi-Image 3D Drift")
        )
        logger.info(
            f"  ⚡ [{role_tag}] {n_shots} shots ({n_shots - 1} nhát cắt: "
            f"{'s -> '.join(f'{d:.2f}' for d in shot_durs)}s | Niche: {niche or 'default'}) từ {len(base_images)} ảnh gốc"
        )
        run_ffmpeg_with_gpu_fallback(args)
        return output_path
    finally:
        for tf in temp_files:
            try:
                Path(tf).unlink(missing_ok=True)
            except Exception:
                pass


def get_hook_live_fx_filter(
    niche: Optional[str] = None,
    is_scifi: bool = False,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    Bộ lọc Hiệu Ứng Chuyển Động Thực phủ trực tiếp lên Cảnh 1 (Hook).
    """
    n_low = (niche or "").lower()
    if any(k in n_low for k in ("history", "war", "lịch sử", "chiến tranh", "alternate")):
        return (
            f"colorbalance=rs=0.07:gs=0.02:bs=-0.04,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.028*sin(15*t)':contrast=1.06:saturation=1.10:eval=frame,"
            f"noise=alls=13:allf=t+u"
        )
    if any(k in n_low for k in ("psych", "tâm lý", "selfhelp", "mind", "brain")):
        return (
            f"rgbashift=rh=5:bh=-5:rv=2:bv=-2,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.032*sin(12.5*t)':contrast=1.07:eval=frame,"
            f"noise=alls=9:allf=t+u"
        )
    if is_scifi or any(k in n_low for k in ("myster", "bí ẩn", "ocean", "space", "scifi")):
        return (
            f"colorbalance=bs=0.07:gs=0.03,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.030*sin(9*t)':contrast=1.06:eval=frame,"
            f"noise=alls=12:allf=t+u"
        )
    return (
        f"setsar=1,"
        f"scale={width}:{height},setsar=1,"
        f"eq=brightness='0.02*sin(10*t)':contrast=1.04:eval=frame,"
        f"noise=alls=8:allf=t+u"
    )


def multi_pose_loop_to_video(
    image_paths: List[str],
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    switch_interval: float = 0.28,
    niche: Optional[str] = None,
) -> str:
    """
    Render chuỗi đa khung hình (pose_images) với tối thiểu 3 nhát cắt chuyển cảnh sắc nét.
    """
    return render_opening_hook_multi_cut(
        image_paths=image_paths,
        duration_seconds=duration_seconds,
        output_path=output_path,
        width=width,
        height=height,
        fps=fps,
        min_cuts=3,
        niche=niche,
    )
