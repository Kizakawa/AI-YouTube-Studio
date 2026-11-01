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
            # Hoạt họa 2D & Truyện tranh châm biếm: nét mực sắc nét, tương phản sạch sẽ, tôn vinh nét vẽ 2D
            "vf_filter": "eq=contrast=1.08:saturation=1.05:brightness=0.01,unsharp=3:3:0.6:3:3:0.25",
            "vignette": "",
        },
        "psychology": {
            # High-Contrast Neo-Noir & Chiaroscuro:
            # Giữ độ bão hòa tự nhiên vừa phải (saturation=0.72 thay vì 0.18 xám xịt) + nâng sáng nhẹ (+0.02)
            # để ánh mắt, vi biểu cảm khuôn mặt và tương phản nổi bật tức thì ở giây 0.0s trên Shorts Feed
            "vf_filter": "eq=contrast=1.18:saturation=0.72:brightness=0.02,colorbalance=rs=-0.01:gs=0.00:bs=0.03,unsharp=5:5:0.70:3:3:0.25",
            "vignette": "vignette=0.22",
        },
        "deep_sea": {
            # Vực thẳm đại dương & Bí ẩn biển sâu: nâng sáng (+0.02) và bão hòa (1.18) để chủ thể dưới nước / phát quang
            # nổi rõ ngay từ khung hình 0.0s, tránh bị tối đen khi xem trên điện thoại
            "vf_filter": "eq=contrast=1.16:saturation=1.18:brightness=0.02,colorbalance=rs=-0.03:gs=0.01:bs=0.06,unsharp=3:3:0.65:3:3:0.22",
            "vignette": "vignette=0.24",
        },
        "wildlife_predators": {
            # National Geographic: màu sắc sống động rực rỡ, sắc nét tự nhiên, khung hình sáng rõ
            "vf_filter": "eq=contrast=1.08:saturation=1.22:brightness=0.02,unsharp=3:3:0.55:3:3:0.20",
            "vignette": "",
        },
        "animal_rescue": {
            # Cảm xúc ấm áp: ánh sáng vàng ấm nhẹ, màu sắc tươi tắn, không tối góc
            "vf_filter": "eq=contrast=1.04:saturation=1.15:brightness=0.03,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "history_mystery": {
            # Cổ tích & Huyền bí: ánh vàng kim ấm áp, giữ độ sáng và chi tiết kiến trúc/nhân vật
            "vf_filter": "eq=contrast=1.10:saturation=1.14:brightness=0.02,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.55:3:3:0.20",
            "vignette": "vignette=0.20",
        },
        "horror": {
            # Rùng rợn & Kinh dị: tương phản gai góc nhưng giữ chi tiết vùng tối rõ nét (+0.01 brightness)
            "vf_filter": "eq=contrast=1.18:saturation=0.88:brightness=0.01,colorbalance=rs=-0.02:gs=0.00:bs=0.04,unsharp=3:3:0.70:3:3:0.25",
            "vignette": "vignette=0.28",
        },
        "scifi": {
            # Viễn tưởng công nghệ: neon rực rỡ, màu sắc punchy tươi sáng, chi tiết sắc nét
            "vf_filter": "eq=contrast=1.08:saturation=1.22:brightness=0.02,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "vignette=0.20",
        },
        "luxury": {
            # Sang trọng & Động lực: ánh vàng kim rạng rỡ, màu sắc cao cấp, độ sáng hoàn hảo
            "vf_filter": "eq=contrast=1.05:saturation=1.16:brightness=0.02,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "stoic": {
            # Khắc kỷ / Triết học: tông trầm tĩnh Chiaroscuro sang trọng, rõ nét chi tiết
            "vf_filter": "eq=contrast=1.16:saturation=0.58:brightness=0.01,colorbalance=rs=-0.01:gs=0.00:bs=0.02,unsharp=5:5:0.65:3:3:0.20",
            "vignette": "vignette=0.22",
        },
        "health": {
            # Sức khỏe & Sinh học: màu sắc tươi tắn, trong trẻo, căng tràn sức sống
            "vf_filter": "eq=contrast=1.04:saturation=1.20:brightness=0.03,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "general": {
            # Mặc định cinema: màu sắc tươi tắn, trong trẻo, tôn vinh ảnh web và ảnh AI gốc
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
# CHECKPOINT A: Ảnh tĩnh → Video (Ken Burns)
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
    Biến 1 ảnh tĩnh → video MP4 với Ken Burns cinematic effect và bộ lọc thị giác viễn tưởng / Niche Grading.
    
    Hỗ trợ các loại chuyển động:
      - zoom_in:        Zoom dần vào tâm ảnh (mặc định, cảm giác áp lực/sức mạnh)
      - zoom_out:       Zoom ra xa (cảm giác khám phá/vĩ đại)
      - pan_left:       Lướt từ phải sang trái (cinematic reveal)
      - pan_right:      Lướt từ trái sang phải
      - pan_up:         Kéo từ dưới lên (climax/rising)
      - pan_down:       Kéo từ trên xuống (tụt xuống/ominous)
      - hyper_zoom:     Tăng tốc lao nhanh vào trung tâm (Warp speed / vượt chân trời sự kiện)
      - warp_zoom:      Zoom động lực cao bước nhảy không gian
      - cinematic_drift: Trôi dạt không trọng lực 3D (kết hợp panning ngang dọc và zoom chậm)
    
    Khi is_hook_scene=True: Áp dụng cú Punch-in Zoom kịch tính (1.05 -> 1.38)
    nhằm kích thích thị giác, khóa chặt sự chú ý của người xem trong 2.5s đầu.
    Khi is_scifi=True: Tự động kích hoạt bộ lọc độ tương phản cao, làm rực sắc màu neon/sao vũ trụ
    và tăng độ sắc nét chi tiết 8K (HDR Sci-Fi grading).
    Codec: H.264 + yuv420p (YouTube compatible).
    """
    # ── Chuẩn hóa ảnh bằng Pillow: chuyển sang RGB, Supersampling 2.0x (2160x3840) khử rung zoompan ──
    # Bộ lọc zoompan của FFmpeg làm tròn tọa độ (x, y) về số nguyên pixel trên ảnh đầu vào.
    # Khi chuẩn bị ảnh ở độ phân giải 2.0x (2160x3840 - 4K Vertical) một lần duy nhất bằng Pillow LANCZOS,
    # mật độ lưới tọa độ tăng gấp 4 lần (bước nhảy 0.5px sub-pixel khi hạ về 1080x1920), triệt tiêu 100% hiện tượng rung/giật bậc thang!
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
            # Khi tắt zoompan nhưng bật zoom_effect, tạo biên đệm +36px để lướt camera (smooth crop drift)
            # mượt mà, triệt tiêu cả lỗi rung pixel của zoompan lẫn lỗi khung hình chết đứng (freeze-frame)
            if enable_zoompan:
                scale_w = int(width * 1.35)
                scale_h = int(height * 1.35)
                max_up = 3.8 * 1.35
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

    d = max(int(duration_seconds * fps) + 2, fps)
    z_start = 1.00
    z_end = 1.08

    # Khi tắt zoompan nhưng zoom_effect=True: dùng phép dịch chuyển cửa sổ crop (+36px) mượt mà theo camera_motion
    if not enable_zoompan:
        if zoom_effect:
            dur_f = max(float(duration_seconds), 0.8)
            motion_key = (camera_motion or "zoom_in").lower()
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
    elif camera_motion == "zoom_out":
        zoompan = (
            f"zoompan=z='{z_end}-({z_end}-{z_start})*(on/{d})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    else:
        zoompan = (
            f"zoompan=z='{z_start}+({z_end}-{z_start})*(on/{d})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )

    # Hiệu ứng màu sắc điện ảnh và chiều sâu theo Niche Profile (100% Free Native FFmpeg Filter)
    grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi, is_hook_scene=is_hook_scene)
    import re as _re
    clean_vf_filter = _re.sub(r",?unsharp=[^,]+", "", grading["vf_filter"]).strip(",")
    if enable_zoompan:
        vf_parts = [clean_vf_filter] if clean_vf_filter else []
        if grading.get("vignette"):
            vf_parts.append(grading['vignette'])
        vf_parts.append(zoompan)
    else:
        vf_parts = [zoompan]
        if clean_vf_filter:
            vf_parts.append(clean_vf_filter)
        if grading.get("vignette"):
            vf_parts.append(grading['vignette'])

    vf_chain = ",".join(vf_parts) + ",format=yuv420p"

    enc_args = get_video_encoder_args(quality="intermediate")
    loop_args = ["-loop", "1"] if not enable_zoompan else []
    args = [
        *loop_args,
        "-i", image_path,
        "-vf", vf_chain,
        "-t", str(duration_seconds),
        *enc_args,
        *BT709_COLOR_ARGS,
        "-r", str(fps),
        "-an",
        output_path
    ]
    try:
        run_ffmpeg_with_gpu_fallback(args)
    finally:
        # Xóa file tạm sau khi render xong (tránh rác file)
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
    Render phân cảnh đa khung hình (Multi-Image Cut) cho cả Cảnh 1 (Hook) lẫn các Cảnh thân (Body Scenes 2..N)
    để đạt mật độ 15–17 ảnh độc lập / video ~40s mà KHÔNG cần cắt vụn câu thoại TTS:
    - Với Cảnh 1 (`is_hook_scene=True`): Render tối thiểu 4 shots (3 nhát cắt chuyển cảnh dồn dập ~0.85s–1.1s/shot) + Live FX.
    - Với Cảnh thân (`is_hook_scene=False`): Render chính xác số lượng ảnh thật độc lập trong `image_paths` (2–3 ảnh/cảnh,
      đổi khung hình mỗi ~1.8s–2.2s) kèm chuyển động máy quay 3D mượt mà trên từng ảnh + Niche Color Grading.
    """
    import re as _re
    import uuid as _uuid
    from PIL import Image, ImageEnhance
    from app.services.media.image_quality import prepare_vertical

    valid_paths = [
        p for p in (image_paths or [])
        if p and Path(p).exists() and Path(p).stat().st_size > 1000
    ]
    if not valid_paths:
        raise ValueError("render_opening_hook_multi_cut: Không có file ảnh hợp lệ")

    # Tự động tính số lượng ảnh hiển thị tối ưu theo thời lượng câu đọc (mỗi ảnh >= 2.0s, tối đa 2 ảnh/cảnh):
    # - Nếu cảnh >= 3.0s có 1 ảnh gốc và min_cuts >= 1: thực hiện 2-Shot Dynamic Camera Cut (Toàn cảnh 1.00x -> Focal Punch-In 1.28x ở giữa cảnh).
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

    # 4 góc máy điện ảnh (zoom_factor, center_x_ratio, center_y_ratio, contrast_boost)
    CAMERA_ANGLES = [
        (1.00, 0.50, 0.50, 1.00),  # Shot 1 (0.0s): Toàn cảnh 9:16 sắc nét (Establishing Hook)
        (1.28, 0.50, 0.34, 1.04),  # Cut #1 (~0.85s): Cận cảnh 1/3 trên (Mắt/Khuôn mặt/Chủ thể chính)
        (1.38, 0.38, 0.52, 1.06),  # Cut #2 (~1.70s): Góc máy trung cảnh lệch trục (Dynamic Counter-Shot)
        (1.52, 0.56, 0.40, 1.08),  # Cut #3 (~2.60s): Đặc tả cực cận cao trào (Extreme Focal Punch-In)
    ]

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
        cropped = base_img.crop((left, top, left + cw, top + ch)).resize((width, height), Image.LANCZOS)
        if abs(contrast - 1.0) > 0.01:
            cropped = ImageEnhance.Contrast(cropped).enhance(contrast)
        return cropped

    try:
        # Bước 1: Chuẩn hóa toàn bộ các ảnh gốc sang khung 9:16 (1080x1920)
        base_images: List[Image.Image] = []
        for p in valid_paths[:n_shots]:
            try:
                with Image.open(p) as im:
                    if im.mode != "RGB":
                        im = im.convert("RGB")
                    norm_im = prepare_vertical(im, width, height, max_upscale=3.8, is_hook_scene=is_hook_scene)
                    base_images.append(norm_im)
            except Exception as ex_im:
                logger.debug(f"[render_opening_hook_multi_cut] Lỗi đọc ảnh '{p}': {ex_im}")

        if not base_images:
            raise ValueError(f"Không thể chuẩn hóa bất kỳ ảnh nào từ {valid_paths}")

        # Bước 2: Tạo đủ n_shots khung hình độc lập
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

        # Bước 3: Phân bổ thời lượng từng shot mượt mà
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

        # Bước 4: Ghép n_shots bằng 1 lệnh FFmpeg filter_complex duy nhất + Niche Color Grading
        grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi, is_hook_scene=is_hook_scene)
        clean_vf_filter = _re.sub(r",?unsharp=[^,]+", "", grading["vf_filter"]).strip(",")
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
        pad_w = width + 72
        pad_h = height + 128
        drift_exprs = [
            ("60-18*t", "104-30*t"),
            ("12+18*t", "24+30*t"),
            ("60-18*t", "24+30*t"),
            ("12+18*t", "104-30*t"),
        ]

        for i, (sp, sd) in enumerate(zip(shot_paths, shot_durs)):
            input_args.extend(["-loop", "1", "-t", f"{sd:.3f}", "-i", sp])
            if False:
                concat_inputs.append(f"[{i}:v]")
            else:
                sd_safe = max(float(sd), 1.0)
                drift_scaled = [
                    (f"60-48*min(t/{sd_safe:.2f},1)", f"104-80*min(t/{sd_safe:.2f},1)"),
                    (f"12+48*min(t/{sd_safe:.2f},1)", f"24+80*min(t/{sd_safe:.2f},1)"),
                    (f"60-48*min(t/{sd_safe:.2f},1)", f"24+80*min(t/{sd_safe:.2f},1)"),
                    (f"12+48*min(t/{sd_safe:.2f},1)", f"104-80*min(t/{sd_safe:.2f},1)"),
                ]
                dx, dy = drift_scaled[i % len(drift_scaled)]
                per_shot_chains.append(
                    f"[{i}:v]scale={pad_w}:{pad_h},crop={width}:{height}:'{dx}':'{dy}',setsar=1,fps={fps}[v{i}]"
                )
                concat_inputs.append(f"[v{i}]")

        if per_shot_chains:
            filter_complex = f"{';'.join(per_shot_chains)};{''.join(concat_inputs)}concat=n={n_shots}:v=1:a=0,{post_chain}[vout]"
        else:
            filter_complex = f"{''.join(concat_inputs)}concat=n={n_shots}:v=1:a=0,{post_chain}[vout]"

        enc_args = get_video_encoder_args(quality="intermediate")
        args = [
            *input_args,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-t", f"{duration_seconds:.3f}",
            *enc_args,
            "-r", str(fps),
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
    Bộ lọc Hiệu Ứng Chuyển Động Thực (Live Particle / Lửa / Khói / Sóng Âm / Heartbeat Glitch)
    phủ trực tiếp lên Cảnh 1 (4 ảnh Google Top Search cắt nhanh) cho nhóm kênh Lịch Sử, Tâm Lý, Bí Ẩn:
    - Lịch sử / Chiến tranh (history, alternate_history):
      Rung chấn nhẹ (Camera Shake) + Chớp lửa bập bùng (Fire Flicker) + Khói/Hạt bụi động (Smoke Grain).
    - Bí ẩn / Đại dương (mystery, mysteries, scifi):
      Trôi nổi đáy sâu (Deep Drift) + Quét tia Sonar chớp sáng (Sonar Pulse Sweep) + Hạt bụi nước (Floating Particles).
    - Tâm lý (psychology, selfhelp):
      Nhiễu sóng thần kinh (Chromatic Aberration RGB Shift) + Nhịp đập thị giác (Heartbeat Pulse).
    """
    n_low = (niche or "").lower()
    if any(k in n_low for k in ("history", "war", "lịch sử", "chiến tranh", "alternate")):
        # Rung chấn Camera Shake + Chớp lửa ấm bập bùng + Khói chuyển động
        return (
            f"colorbalance=rs=0.07:gs=0.02:bs=-0.04,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.028*sin(15*t)':contrast=1.06:saturation=1.10:eval=frame,"
            f"noise=alls=13:allf=t+u"
        )
    if any(k in n_low for k in ("psych", "tâm lý", "selfhelp", "mind", "brain")):
        # Nhịp đập thị giác (Heartbeat Pulse ~2Hz) + Nhiễu sóng thần kinh (Chromatic Glitch)
        return (
            f"rgbashift=rh=5:bh=-5:rv=2:bv=-2,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.032*sin(12.5*t)':contrast=1.07:eval=frame,"
            f"noise=alls=9:allf=t+u"
        )
    if is_scifi or any(k in n_low for k in ("myster", "bí ẩn", "ocean", "space", "scifi")):
        # Hạt bụi nước trôi nổi + Quét tia Sonar / Ánh sáng chớp động
        return (
            f"colorbalance=bs=0.07:gs=0.03,"
            f"setsar=1,"
            f"scale={width}:{height},setsar=1,"
            f"eq=brightness='0.030*sin(9*t)':contrast=1.06:eval=frame,"
            f"noise=alls=12:allf=t+u"
        )
    # Mặc định cho các kênh khác khi dùng ảnh tĩnh ở Hook: rung động nhẹ + hạt phim sống động
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

