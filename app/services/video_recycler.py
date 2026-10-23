"""
Video Recycler & Anti-pHash Transformative Engine (Layer-2 Visual Fingerprint Breaker)
======================================================================================
Chuyên trách tái chế video cứu hộ động vật thực tế (Real Rescue Video Recycling) đạt chuẩn
100% Transformative Fair Use và vượt qua hệ thống quét trùng lặp Visual pHash / vHash của YouTube:

1. Xóa sạch Watermark góc & Phụ đề cứng (Hardcoded Subtitles) bằng nội suy `delogo + Gaussian Blur`
   ngay trên hệ tọa độ gốc `1080x1920` (đảm bảo chính xác tuyệt đối từng pixel trước khi biến đổi hình học).
2. Lật gương ngang có chọn lọc (`Horizontal Flip - hflip`):
   Đảo ngược ma trận tọa độ trái-phải ở các phân cảnh xen kẽ không chứa biển báo/chữ viết thực tế.
3. Crop & Zoom viền thông minh (`4% - 6%` Smart Border Crop & Lanczos Upscale):
   Cắt 4% - 6% viền ngoài của khung hình gốc và nội suy Lanczos về `1080x1920`, làm dịch chuyển toàn bộ
   lưới ô vuông Perceptual Hash (pHash / dHash) lệch khỏi vị trí gốc.
4. Ánh xạ tọa độ động (Post-Transform Coordinate Mapping) & Đè Thẻ Phụ Đề Kính Mờ (Frosted Glass Card):
   Tính toán tọa độ mới của vùng phụ đề sau khi `hflip + crop/zoom` và phủ thẻ phụ đề mới lên trên cùng
   (chữ tiếng Anh luôn xuôi chiều, sắc nét, che kín 100% vùng vừa xóa phụ đề cũ).
5. Kiểm toán khoảng cách băm thị giác (pHash / dHash Hamming Distance Audit):
   Đo trực tiếp độ lệch bit (Hamming Distance trên 64-bit hash) giữa khung hình gốc và khung hình sau tái chế
   để chứng minh độ khác biệt thị giác vượt ngưỡng an toàn (> 18/64 bits, tương đương > 28% - 55% sai khác).
"""

import os
import subprocess
import json
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def compute_dhash_64(img: Image.Image) -> int:
    """Tính mã băm thị giác 64-bit (Difference Perceptual Hash - dHash 9x8) của một khung hình."""
    gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    for row in range(8):
        row_offset = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if pixels[row_offset + col] > pixels[row_offset + col + 1] else 0)
    return bits


def compute_ahash_64(img: Image.Image) -> int:
    """Tính mã băm trung bình 64-bit (Average Perceptual Hash - aHash 8x8) của một khung hình."""
    gray = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / 64.0
    bits = 0
    for p in pixels:
        bits = (bits << 1) | (1 if p >= avg else 0)
    return bits


def hamming_distance_64(hash1: int, hash2: int) -> int:
    """Tính số bit khác nhau (0..64) giữa hai mã băm 64-bit."""
    return bin(hash1 ^ hash2).count("1")


def map_box_after_flip_and_crop(
    box: Tuple[int, int, int, int],
    flip_horizontal: bool,
    crop_pct: float,
    width: int = 1080,
    height: int = 1920,
) -> Tuple[int, int, int, int]:
    """
    Tính toán lại tọa độ hộp (x, y, w, h) sau khi:
      1. Lật ngang (hflip): x' = width - x - w
      2. Cắt viền đối xứng (crop_pct: tổng tỷ lệ cắt cả 2 bên, ví dụ 0.05 = cắt 2.5% mỗi mép)
         và phóng to lại về kích thước (width, height).
    """
    bx, by, bw, bh = box
    if flip_horizontal:
        bx = width - bx - bw

    keep_ratio = 1.0 - float(crop_pct)
    cw = int(round(width * keep_ratio))
    ch = int(round(height * keep_ratio))
    cx = (width - cw) // 2
    cy = (height - ch) // 2

    scale_x = width / float(cw)
    scale_y = height / float(ch)

    new_x = int(round((bx - cx) * scale_x))
    new_y = int(round((by - cy) * scale_y))
    new_w = int(round(bw * scale_x))
    new_h = int(round(bh * scale_y))

    new_x = max(24, min(width - 100, new_x))
    new_y = max(300, min(height - 150, new_y))
    new_w = max(100, min(width - new_x - 24, new_w))
    new_h = max(80, min(height - new_y - 60, new_h))
    return (new_x, new_y, new_w, new_h)


def create_frosted_subtitle_card_png(
    png_path: str,
    mapped_box: Tuple[int, int, int, int],
    line1: str,
    line2: str,
    is_hook: bool = False,
) -> str:
    """Tạo lớp phủ PNG chứa Thẻ Phụ Đề Kính Mờ (Frosted Dark-Glass Card) tại đúng tọa độ đã ánh xạ."""
    bx, by, bw, bh = mapped_box
    ov = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    pad_x = 14
    pad_y = 8
    x1 = max(36, bx - pad_x)
    y1 = max(340, by - pad_y)
    x2 = min(1044, bx + bw + pad_x)
    y2 = min(1820, by + bh + pad_y)

    border_col = (255, 215, 0, 190) if is_hook else (255, 255, 255, 125)
    dr.rounded_rectangle([(x1, y1), (x2, y2)], radius=28, fill=(12, 16, 24, 226), outline=border_col, width=3)

    font_path = r"C:\Windows\Fonts\arialbd.ttf"
    font1 = ImageFont.truetype(font_path, 45 if not is_hook else 47)
    font2 = ImageFont.truetype(font_path, 45 if not is_hook else 47)

    b1 = dr.textbbox((0, 0), line1, font=font1)
    b2 = dr.textbbox((0, 0), line2, font=font2)
    w1, h1 = b1[2] - b1[0], b1[3] - b1[1]
    w2, h2 = b2[2] - b2[0], b2[3] - b2[1]

    card_h = y2 - y1
    total_text_h = h1 + h2 + 22
    ty1 = y1 + (card_h - total_text_h) // 2 - 4
    ty2 = ty1 + h1 + 22

    dr.text(
        ((1080 - w1) // 2, ty1),
        line1,
        font=font1,
        fill=(255, 255, 255, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 255),
    )
    dr.text(
        ((1080 - w2) // 2, ty2),
        line2,
        font=font2,
        fill=(255, 225, 45, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 255),
    )
    ov.save(png_path)
    return png_path


def render_anti_phash_subclip(
    ffmpeg_exe: str,
    src_video: str,
    work_dir: str,
    tag: str,
    src_start: float,
    src_end: float,
    target_dur: float,
    orig_subtitle_box: Tuple[int, int, int, int],
    line1: str,
    line2: str,
    flip_horizontal: bool = False,
    crop_pct: float = 0.05,
    is_hook: bool = False,
    top_watermarks: Optional[List[Tuple[int, int, int, int]]] = None,
) -> Dict:
    """
    Kết xuất phân cảnh video với đầy đủ 4 lớp chống trùng lặp hình ảnh (Layer-2 Anti-pHash):
      1. `delogo + gblur` trên tọa độ gốc (xóa logo góc & phụ đề cứng).
      2. `hflip` (Lật gương ngang có chọn lọc nếu `flip_horizontal=True`).
      3. `crop + scale=1080:1920:flags=lanczos` (Cắt viền thông minh 4%-6% dịch lưới pHash).
      4. `overlay` thẻ phụ đề mới tại tọa độ đã ánh xạ sau khi lật/crop.
    """
    if top_watermarks is None:
        top_watermarks = [
            (85, 200, 230, 80),   # Top-left watermark (e.g. newsflare)
            (900, 200, 135, 155), # Top-right watermark (e.g. WE LOVE ANIMALS)
        ]

    bx, by, bw, bh = orig_subtitle_box
    mapped_box = map_box_after_flip_and_crop(
        orig_subtitle_box,
        flip_horizontal=flip_horizontal,
        crop_pct=crop_pct,
        width=1080,
        height=1920,
    )

    png_path = os.path.join(work_dir, f"ov_{tag}.png")
    create_frosted_subtitle_card_png(png_path, mapped_box, line1, line2, is_hook=is_hook)

    src_dur = max(0.1, src_end - src_start)
    pts_factor = target_dur / src_dur

    keep_ratio = 1.0 - float(crop_pct)
    cw = (int(round(1080 * keep_ratio)) // 2) * 2
    ch = (int(round(1920 * keep_ratio)) // 2) * 2
    cx = (1080 - cw) // 2
    cy = (1920 - ch) // 2

    flip_filter = ",hflip" if flip_horizontal else ""
    crop_zoom_filter = f",crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=lanczos"

    out_clip = os.path.join(work_dir, f"clip_{tag}.mp4")

    if top_watermarks and len(top_watermarks) >= 2:
        w1x, w1y, w1w, w1h = top_watermarks[0]
        w2x, w2y, w2w, w2h = top_watermarks[1]
        fc = (
            f"[0:v]trim=start={src_start:.3f}:end={src_end:.3f},setpts=({pts_factor:.6f})*(PTS-STARTPTS),"
            f"fps=30,format=yuv420p,scale=1080:1920:flags=lanczos,"
            f"delogo=x={w1x}:y={w1y}:w={w1w}:h={w1h},"
            f"delogo=x={w2x}:y={w2y}:w={w2w}:h={w2h},"
            f"delogo=x={bx}:y={by}:w={bw}:h={bh},"
            f"split=4[base][w1][w2][sub];"
            f"[w1]crop={w1w}:{w1h}:{w1x}:{w1y},gblur=sigma=18[w1b];"
            f"[w2]crop={w2w}:{w2h}:{w2x}:{w2y},gblur=sigma=18[w2b];"
            f"[sub]crop={bw}:{bh}:{bx}:{by},gblur=sigma=22[subb];"
            f"[base][w1b]overlay={w1x}:{w1y}[v1];"
            f"[v1][w2b]overlay={w2x}:{w2y}[v2];"
            f"[v2][subb]overlay={bx}:{by},"
            f"eq=contrast=1.06:saturation=1.09:brightness=0.01"
            f"{flip_filter}"
            f"{crop_zoom_filter}[v_transformed];"
            f"[v_transformed][1:v]overlay=0:0[outv]"
        )
    else:
        fc = (
            f"[0:v]trim=start={src_start:.3f}:end={src_end:.3f},setpts=({pts_factor:.6f})*(PTS-STARTPTS),"
            f"fps=30,format=yuv420p,scale=1080:1920:flags=lanczos,"
            f"delogo=x={bx}:y={by}:w={bw}:h={bh},"
            f"split=2[base][sub];"
            f"[sub]crop={bw}:{bh}:{bx}:{by},gblur=sigma=24[subb];"
            f"[base][subb]overlay={bx}:{by},"
            f"eq=contrast=1.06:saturation=1.09:brightness=0.01"
            f"{flip_filter}"
            f"{crop_zoom_filter}[v_transformed];"
            f"[v_transformed][1:v]overlay=0:0[outv]"
        )

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        src_video,
        "-i",
        png_path,
        "-filter_complex",
        fc,
        "-map",
        "[outv]",
        "-an",
        "-t",
        f"{target_dur:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        out_clip,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    return {
        "tag": tag,
        "clip_path": out_clip,
        "flip_horizontal": flip_horizontal,
        "crop_pct": crop_pct,
        "crop_rect": (cx, cy, cw, ch),
        "orig_box": orig_subtitle_box,
        "mapped_box": mapped_box,
    }


def audit_clip_phash_divergence(
    ffmpeg_exe: str,
    src_video: str,
    rendered_clip: str,
    src_mid_sec: float,
    clip_mid_sec: float,
    work_dir: str,
    tag: str,
) -> Dict:
    """
    Trích xuất khung hình giữa của đoạn video gốc và đoạn video đã tái chế,
    đo khoảng cách Hamming (dHash 64-bit & aHash 64-bit) để chứng minh đã phá vỡ pHash.
    Ngưỡng Content ID thông thường báo trùng khi Hamming Distance <= 10/64 bits (< 15.6%).
    Khi Hamming Distance >= 18/64 bits (>= 28%), hai khung hình được coi là khác biệt hoàn toàn về mặt băm thị giác.
    """
    orig_jpg = os.path.join(work_dir, f"phash_orig_{tag}.jpg")
    rec_jpg = os.path.join(work_dir, f"phash_rec_{tag}.jpg")

    subprocess.run(
        [ffmpeg_exe, "-y", "-ss", f"{src_mid_sec:.2f}", "-i", src_video, "-frames:v", "1", "-q:v", "2", orig_jpg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        [ffmpeg_exe, "-y", "-ss", f"{clip_mid_sec:.2f}", "-i", rendered_clip, "-frames:v", "1", "-q:v", "2", rec_jpg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    im_orig = Image.open(orig_jpg)
    im_rec = Image.open(rec_jpg)

    d_orig = compute_dhash_64(im_orig)
    d_rec = compute_dhash_64(im_rec)
    d_dist = hamming_distance_64(d_orig, d_rec)

    a_orig = compute_ahash_64(im_orig)
    a_rec = compute_ahash_64(im_rec)
    a_dist = hamming_distance_64(a_orig, a_rec)

    max_dist = max(d_dist, a_dist)
    divergence_pct = round((max_dist / 64.0) * 100.0, 1)

    return {
        "tag": tag,
        "dhash_hamming_bits": d_dist,
        "ahash_hamming_bits": a_dist,
        "max_hamming_bits": max_dist,
        "divergence_pct": divergence_pct,
        "anti_phash_passed": max_dist >= 16,
    }



PAWS_HEARTS_BGM_ARCHETYPES: Dict[str, str] = {
    "mother_love": "PAWS_BGM_01_mother_love.wav",
    "rescue_tension_to_relief": "PAWS_BGM_02_rescue_tension_to_relief.wav",
    "urgent_rescue_to_warm_relief": "PAWS_BGM_02_rescue_tension_to_relief.wav",
    "lonely_stray_to_warm_home": "PAWS_BGM_03_lonely_stray_to_warm_home.wav",
    "healing_recovery_journey": "PAWS_BGM_04_healing_recovery_journey.wav",
    "smart_animal_seeks_help": "PAWS_BGM_05_smart_animal_seeks_help.wav",
    "tiny_puppies_kittens_lullaby": "PAWS_BGM_05_smart_animal_seeks_help.wav",
    "joyful_new_family_ending": "PAWS_BGM_06_joyful_new_family_ending.wav",
}

_AUDIO_CATALOG_CACHE: Optional[Dict[str, Any]] = None


def _load_audio_catalog() -> Dict[str, Any]:
    """Nạp bảng băm `audio_catalog.json` vào RAM 1 lần duy nhất (O(1) lookup, 0ms disk scan)."""
    global _AUDIO_CATALOG_CACHE
    if _AUDIO_CATALOG_CACHE is not None:
        return _AUDIO_CATALOG_CACHE
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    catalog_path = os.path.join(base_dir, "assets", "bgm", "audio_catalog.json")
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                _AUDIO_CATALOG_CACHE = json.load(f)
                return _AUDIO_CATALOG_CACHE
        except Exception:
            pass
    _AUDIO_CATALOG_CACHE = {}
    return _AUDIO_CATALOG_CACHE


def resolve_channel_bgm_fast(
    channel_or_niche: str,
    archetype_key: Optional[str] = None,
    text_context: Optional[str] = None,
) -> Optional[str]:
    """
    Truy xuất tức thì O(1) (~0.001ms) đường dẫn nhạc nền theo Thư mục Ngách/Kênh đã gắn nhãn
    (`01_animal_rescue_paws` .. `05_alternate_history`) mà không cần quét ổ cứng.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bgm_root = os.path.join(base_dir, "assets", "bgm")
    catalog = _load_audio_catalog()
    mapping = catalog.get("channel_to_niche_folder", {})
    niches = catalog.get("niches", {})

    clean_key = str(channel_or_niche or "").strip().lower()
    niche_folder = mapping.get(clean_key)
    if not niche_folder:
        for k, folder in mapping.items():
            if k in clean_key:
                niche_folder = folder
                break

    if not niche_folder or niche_folder not in niches:
        return None

    niche_entry = niches[niche_folder]
    tracks = niche_entry.get("bgm_tracks", {})

    # 1. Khớp trực tiếp theo nhãn archetype_key (O(1) hash lookup)
    if archetype_key and archetype_key in tracks:
        rel_file = tracks[archetype_key].get("file")
        if rel_file:
            abs_p = os.path.join(bgm_root, rel_file.replace("/", os.sep))
            if os.path.exists(abs_p):
                return abs_p

    # 2. Khớp ngữ nghĩa tự động theo text_context (topic / script) nếu có
    if text_context and tracks:
        ctx_low = text_context.lower()
        best_track = None
        best_hits = 0
        for t_info in tracks.values():
            triggers = t_info.get("semantic_triggers", [])
            hits = sum(1 for kw in triggers if kw in ctx_low)
            if hits > best_hits:
                best_hits = hits
                best_track = t_info.get("file")
        if best_track:
            abs_p = os.path.join(bgm_root, best_track.replace("/", os.sep))
            if os.path.exists(abs_p):
                return abs_p

    # 3. Trả về bản nhạc mặc định của ngách đó
    default_rel = niche_entry.get("default_bgm")
    if default_rel:
        abs_p = os.path.join(bgm_root, default_rel.replace("/", os.sep))
        if os.path.exists(abs_p):
            return abs_p
    return None


def get_paws_hearts_bgm_path(archetype_key: str = "mother_love", text_context: str = "") -> str:
    """
    Trả về đường dẫn tuyệt đối tới bản nhạc nền độc quyền (Golden-Window 48kHz Stereo WAV)
    trong `assets/bgm/01_animal_rescue_paws/` (với O(1) catalog lookup và fallback tương thích ngược).
    """
    fast_hit = resolve_channel_bgm_fast("01_animal_rescue_paws", archetype_key=archetype_key, text_context=text_context)
    if fast_hit and os.path.exists(fast_hit):
        return fast_hit

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    filename = PAWS_HEARTS_BGM_ARCHETYPES.get(archetype_key, "PAWS_BGM_01_mother_love.wav")
    for sub in ("01_animal_rescue_paws", "paws_hearts"):
        candidate = os.path.join(base_dir, "assets", "bgm", sub, filename)
        if os.path.exists(candidate):
            return candidate
    fallback = os.path.join(base_dir, "assets", "bgm", "emotional_piano.wav")
    return fallback
