"""
app.graph.evaluators.visual_critic — Bộ thẩm định chất lượng hình ảnh & tính chân thật.

Kiểm tra:
1. Tính toàn vẹn của file (Pillow raster bitmap verification, no corrupted files)
2. Độ phân giải & tỉ lệ hiển thị (Dimensions >= 720px chiều ngắn, aspect ratio)
3. Tính chân thật thực thể (Entity Match — cấm stock video diễn viên hiện đại khi làm nhân vật lịch sử)
4. Chống trùng lặp ảnh (Duplicate detection giữa các phân cảnh)
5. Độ tương phản & màu sắc (Phát hiện ảnh đen kịt hoặc đơn sắc lỗi)
"""
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from PIL import Image
from app.graph.state import VisualEvaluationReport, SceneVisualScore
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)


class VisualCritic:
    """Thẩm định viên hình ảnh tự động trước khi đưa vào luồng render video."""

    @staticmethod
    def _compute_image_hash(image_path: str) -> str:
        """Tính dhash (difference hash) đơn giản để phát hiện ảnh trùng lặp giữa các scene."""
        try:
            with Image.open(image_path) as img:
                # Thu nhỏ 9x8 grayscale
                resized = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                pixels = list(resized.getdata())
                # So sánh các pixel cạnh nhau
                diff = []
                for row in range(8):
                    for col in range(8):
                        left = pixels[row * 9 + col]
                        right = pixels[row * 9 + col + 1]
                        diff.append("1" if left > right else "0")
                return "".join(diff)
        except Exception:
            return ""

    @staticmethod
    def _is_solid_color_or_black(image_path: str) -> bool:
        """Phát hiện ảnh bị đen kịt hoàn toàn hoặc ảnh đơn sắc hỏng."""
        try:
            with Image.open(image_path) as img:
                # Lấy 16x16 thumbnail để tính độ lệch chuẩn
                thumb = img.convert("L").resize((16, 16))
                pixels = list(thumb.getdata())
                mean = sum(pixels) / len(pixels)
                variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
                # Nếu phương sai < 5 -> ảnh hầu như là một màu đồng nhất (đen/trắng)
                return variance < 5.0
        except Exception:
            return False

    @staticmethod
    def _measure_sharpness(image_path: str) -> float:
        """
        Đo độ sắc nét biên cạnh (edge sharpness energy) của ảnh bằng bộ lọc FIND_EDGES trên chuẩn hóa 256x256,
        kết hợp đo độ sắc nét vùng chủ thể lấy nét (Top-35% Active Tile Subject-ROI) để các cảnh quay điện ảnh
        xóa phông (shallow depth-of-field / bokeh) hoặc nền tối (low-key noir) không bị chấm oan là mờ nhòe
        chỉ vì nền phía sau phẳng mịn.
        """
        try:
            from PIL import ImageFilter, ImageStat
            with Image.open(image_path) as img:
                gray = img.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                edges = gray.filter(ImageFilter.FIND_EDGES)
                whole_mean = float(ImageStat.Stat(edges).mean[0])
                tile_means: List[float] = []
                for ty in range(0, 256, 32):
                    for tx in range(0, 256, 32):
                        tile = edges.crop((tx, ty, tx + 32, ty + 32))
                        tile_means.append(float(ImageStat.Stat(tile).mean[0]))
                tile_means.sort(reverse=True)
                top_subject_mean = sum(tile_means[:22]) / 22.0 if tile_means else whole_mean
                return round(max(whole_mean, top_subject_mean * 0.62), 2)
        except Exception:
            return 10.0

    # Ngưỡng Hamming (trên 64 bit) để coi 2 khung hình là "cùng 1 cảnh quay" dù bị nén/scale/crop/LUT khác nhau
    _DUP_HAMMING = 7
    _DUP_HAMMING_MULTI = 10

    @classmethod
    def _media_signature(cls, media_path: str) -> List[str]:
        """
        Chữ ký thị giác của 1 cảnh: ảnh → 1 dHash; video → dHash của 5 khung hình (12%, 30%, 50%, 70%, 88% thời lượng)
        để bắt trọn vẹn cả trường hợp trùng lặp một phần (partial overlap) giữa 2 đoạn cắt của cùng 1 video gốc.
        Bỏ qua khung hình gần như đơn sắc (màn đen) vì mọi màn đen đều giống nhau → báo trùng giả.
        """
        import subprocess
        import tempfile
        p = Path(media_path)
        if p.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
            if cls._is_solid_color_or_black(str(p)):
                return []
            h = cls._compute_image_hash(str(p))
            return [h] if h else []

        sig: List[str] = []
        try:
            from app.utils.ffmpeg_helper import get_duration_ms
            dur = max(get_duration_ms(str(p)) / 1000.0, 0.8)
        except Exception:
            dur = 3.0
        sample_points = (
            min(0.25, dur * 0.06),
            dur * 0.22,
            dur * 0.40,
            dur * 0.60,
            dur * 0.78,
            max(dur - 0.25, dur * 0.94),
        )
        with tempfile.TemporaryDirectory() as td:
            for i, t in enumerate(sample_points):
                frame = str(Path(td) / f"f{i}.jpg")
                try:
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(p),
                         "-frames:v", "1", "-vf", "scale=160:-1", frame],
                        capture_output=True, timeout=15,
                    )
                    if Path(frame).exists() and not cls._is_solid_color_or_black(frame):
                        h = cls._compute_image_hash(frame)
                        if h:
                            sig.append(h)
                except Exception:
                    continue
        return sig

    @classmethod
    def _find_duplicate(cls, sig: List[str], seen: Dict[int, List[str]]) -> Optional[int]:
        """
        Trả về scene_id bị trùng (nếu có):
        - Phát hiện ngay nếu có >= 1 khung hình khớp chặt (Hamming <= _DUP_HAMMING = 7, bắt cả trùng lặp 1 phần video)
        - Hoặc >= 2 khung hình khớp trung bình (Hamming <= _DUP_HAMMING_MULTI = 10, bắt cùng góc máy/cùng cảnh quay).
        """
        if not sig:
            return None
        for prev_id, prev_sig in seen.items():
            if not prev_sig:
                continue
            strict_hits = sum(
                1 for h in sig
                if any(sum(a != b for a, b in zip(h, ph)) <= cls._DUP_HAMMING for ph in prev_sig)
            )
            if strict_hits >= 1:
                return prev_id
            multi_hits = sum(
                1 for h in sig
                if any(sum(a != b for a, b in zip(h, ph)) <= cls._DUP_HAMMING_MULTI for ph in prev_sig)
            )
            if multi_hits >= min(2, len(sig), len(prev_sig)):
                return prev_id
        return None

    @classmethod
    def inspect_clip_before_concat(
        cls,
        video_path: str,
        min_short_edge: int = 720,
        min_sharpness: float = 5.8,
        check_watermark: bool = True,
        allow_snow: bool = True,
        auto_sanitize: bool = True,
    ) -> Dict[str, Any]:
        """
        Cổng kiểm duyệt & xử lý logo/tag 3 bước TRƯỚC khi ghép (Pre-Assembly Clip Gate):
        - Bước 1 (Cắt qua đoạn dính tag): Nếu tag/chữ chỉ xuất hiện ở đầu (Intro) hoặc cuối (Outro TikTok/CapCut)
          trong khi đoạn giữa sạch -> tự động cắt bỏ phần đầu/cuối để giữ lại đoạn sạch.
        - Bước 2 (Xóa delogo + Crop lệch góc): Nếu logo bản quyền nằm ở 4 góc hoặc mép trên/dưới trong khi
          giữa màn hình sạch -> tự động dùng FFmpeg `delogo` xóa logo kết hợp phóng Crop dịch tâm né góc đó.
        - Bước 3 (Cuối cùng mới Bỏ): Nếu chữ/logo nằm chình ình giữa màn hình hoặc sau khi cắt/xóa vẫn không sạch
          -> đánh trượt (`passed=False`) để bỏ video này và lấy ứng viên tiếp theo.
        """
        import shutil
        import subprocess
        import tempfile
        from PIL import ImageChops, ImageFilter, ImageStat
        from app.utils.ffmpeg_helper import get_duration_ms, probe_file

        p = Path(video_path)
        if not p.exists() or p.stat().st_size < 15000:
            return {
                "passed": False,
                "score": 1.0,
                "issues": ["File video clip không tồn tại hoặc dung lượng quá nhỏ (< 15KB)"],
                "width": 0,
                "height": 0,
                "sharpness": 0.0,
                "brightness": 0.0,
            }

        issues: List[str] = []
        width, height = 0, 0
        try:
            info = probe_file(str(p)) or {}
            for st in info.get("streams", []):
                if st.get("codec_type") == "video":
                    width = int(st.get("width") or 0)
                    height = int(st.get("height") or 0)
                    break
        except Exception:
            pass

        if width > 0 and height > 0 and min(width, height) < min_short_edge:
            issues.append(f"Độ phân giải clip thấp ({width}x{height} < {min_short_edge}p)")

        dur = 3.0
        try:
            d_ms = get_duration_ms(str(p))
            if d_ms > 0:
                dur = max(d_ms / 1000.0, 0.6)
        except Exception:
            pass

        sample_times = [
            round(min(0.35, dur * 0.18), 2),
            round(dur * 0.50, 2),
            round(max(dur - 0.35, dur * 0.82), 2),
        ]
        sharp_vals: List[float] = []
        lum_vals: List[float] = []
        con_vals: List[float] = []
        gray_frames: List[Image.Image] = []
        edge_frames: List[Image.Image] = []
        frame_bad_flags: List[bool] = []
        bad_art_reasons: List[str] = []

        with tempfile.TemporaryDirectory() as td:
            for idx_t, t_sec in enumerate(sample_times):
                f_jpg = str(Path(td) / f"chk_{idx_t}.jpg")
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-v", "error", "-y",
                            "-ss", f"{t_sec:.2f}", "-i", str(p),
                            "-frames:v", "1", "-q:v", "3", f_jpg,
                        ],
                        capture_output=True,
                        timeout=10,
                    )
                    if not Path(f_jpg).exists() or Path(f_jpg).stat().st_size < 1000:
                        frame_bad_flags.append(False)
                        continue
                    raw_bytes = Path(f_jpg).read_bytes()
                    this_frame_bad = False
                    if check_watermark:
                        try:
                            from app.services.video_memory_rag import VideoMemoryRAG
                            has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(raw_bytes)
                            if has_bad_art:
                                this_frame_bad = True
                                bad_art_reasons.append(art_reason)
                        except Exception:
                            pass
                    frame_bad_flags.append(this_frame_bad)

                    with Image.open(f_jpg) as im:
                        if width == 0 or height == 0:
                            width, height = im.size
                            if min(width, height) < min_short_edge:
                                issues.append(f"Độ phân giải clip thấp ({width}x{height} < {min_short_edge}p)")
                        g256 = im.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                        st = ImageStat.Stat(g256)
                        lum_vals.append(float(st.mean[0]))
                        con_vals.append(float(st.stddev[0]))
                        e256 = g256.filter(ImageFilter.FIND_EDGES)
                        sharp_vals.append(float(ImageStat.Stat(e256).mean[0]))

                        g_small = im.convert("L").resize((180, 320), Image.Resampling.BILINEAR)
                        gray_frames.append(g_small)
                        edge_frames.append(g_small.filter(ImageFilter.FIND_EDGES))
                        if not allow_snow:
                            rgb_s = im.convert("RGB").resize((180, 320), Image.Resampling.BILINEAR)
                            px_s = rgb_s.load()
                            snow_ratio = sum(
                                1 for y_s in range(100, 300) for x_s in range(180)
                                if min(px_s[x_s, y_s]) >= 175 and (max(px_s[x_s, y_s]) - min(px_s[x_s, y_s])) <= 24
                            ) / (200 * 180)
                            if snow_ratio >= 0.09 and not any("tuyết" in x for x in issues):
                                issues.append(f"Phát hiện nền tuyết trắng lạnh lệch bối cảnh (snow_ratio={snow_ratio:.2f})")
                except Exception:
                    continue

        if not sharp_vals:
            return {
                "passed": False,
                "score": 2.0,
                "issues": ["Không thể trích xuất khung hình hợp lệ từ video clip"],
                "width": width,
                "height": height,
                "sharpness": 0.0,
                "brightness": 0.0,
            }

        avg_sharp = sum(sharp_vals) / len(sharp_vals)
        min_f_sharp = min(sharp_vals)
        avg_lum = sum(lum_vals) / len(lum_vals)
        min_lum = min(lum_vals)
        max_lum = max(lum_vals)
        min_con = min(con_vals)

        if min_lum < 18.0 or avg_lum < 22.0:
            issues.append(f"Clip quá tối hoặc có khung hình đen (brightness={avg_lum:.1f}, min={min_lum:.1f} < 18)")
        if max_lum > 242.0:
            issues.append(f"Clip bị cháy sáng / lóa trắng (max_brightness={max_lum:.1f} > 242)")
        if min_con < 16.0:
            issues.append(f"Clip thiếu tương phản hoặc phẳng màu (contrast={min_con:.1f} < 16)")
        if avg_sharp < min_sharpness or min_f_sharp < 2.2:
            issues.append(
                f"Clip bị mờ nhòe / vỡ hạt (sharpness={avg_sharp:.1f}, min={min_f_sharp:.1f}, yêu cầu >= {min_sharpness})"
            )

        # Quét toàn bộ thời lượng clip bằng FFmpeg blackdetect để chặn tuyệt đối mọi chớp đen / fade-to-black ngắn (>= 0.10s)
        # nằm xen giữa các khung hình mẫu (ngăn chặn 100% lỗi HardBlock VideoQA blackdetect >= 0.15s ở cuối pipeline)
        try:
            import re as _re_bd
            vf_bd = "crop=iw*0.74:ih*0.40:iw*0.13:ih*0.15,blackdetect=d=0.10:pic_th=0.98"
            res_bd = subprocess.run(
                ["ffmpeg", "-v", "info", "-i", str(p), "-vf", vf_bd, "-an", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=12,
            )
            bd_log = (res_bd.stderr or "") + "\n" + (res_bd.stdout or "")
            bd_matches = list(
                _re_bd.finditer(
                    r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)",
                    bd_log,
                )
            )
            if bd_matches:
                max_bd = max(float(m.group(3)) for m in bd_matches)
                if max_bd >= 0.10:
                    # Nếu đoạn đen chỉ nằm ở rìa đầu hoặc rìa cuối và phần sạch còn lại >= 2.2s -> tự động cắt gọt sạch!
                    if auto_sanitize and len(bd_matches) == 1 and dur >= 2.6:
                        bs = float(bd_matches[0].group(1))
                        be = float(bd_matches[0].group(2))
                        trim_ss, trim_len = 0.0, 0.0
                        if be <= dur * 0.35 and (dur - be - 0.05) >= 2.2:
                            trim_ss = round(be + 0.05, 2)
                            trim_len = round(dur - trim_ss, 2)
                        elif bs >= dur * 0.65 and (bs - 0.05) >= 2.2:
                            trim_ss = 0.0
                            trim_len = round(bs - 0.05, 2)
                        if trim_len >= 2.2:
                            tmp_bd = str(p.with_name(f"{p.stem}_nobk{p.suffix}"))
                            subprocess.run(
                                [
                                    "ffmpeg", "-v", "error", "-y",
                                    "-ss", f"{trim_ss:.2f}", "-t", f"{trim_len:.2f}",
                                    "-i", str(p),
                                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an",
                                    tmp_bd,
                                ],
                                capture_output=True,
                                timeout=15,
                                check=False,
                            )
                            if Path(tmp_bd).exists() and Path(tmp_bd).stat().st_size > 15000:
                                shutil.move(tmp_bd, str(p))
                                logger.info(
                                    f"✂️ [BlackFrame Sanitizer] Đã gọt bỏ đoạn chớp đen ({bs:.2f}s->{be:.2f}s) khỏi '{p.name}', "
                                    f"giữ lại đoạn sạch {trim_len:.2f}s!"
                                )
                                return cls.inspect_clip_before_concat(
                                    video_path=str(p),
                                    min_short_edge=min_short_edge,
                                    min_sharpness=min_sharpness,
                                    check_watermark=check_watermark,
                                    allow_snow=allow_snow,
                                    auto_sanitize=False,
                                )
                            Path(tmp_bd).unlink(missing_ok=True)
                    issues.append(f"Clip chứa khoảng tối/chớp đen kéo dài {max_bd:.2f}s (>= 0.10s)")
        except Exception:
            pass

        # Kiểm tra và tự động cắt gọt sạch dải viền đen ngang (letterbox / single-sided black bar) ở đỉnh hoặc đáy khung hình 9:16
        if len(gray_frames) >= 1 and width <= height:
            try:
                gf0 = gray_frames[0]
                top_st = ImageStat.Stat(gf0.crop((0, 0, 180, 24)))
                bot_st = ImageStat.Stat(gf0.crop((0, 296, 180, 320)))
                mid_st = ImageStat.Stat(gf0.crop((0, 80, 180, 240)))
                top_dark = float(top_st.mean[0]) < 16.0 and float(top_st.stddev[0]) < 12.0
                bot_dark = float(bot_st.mean[0]) < 16.0 and float(bot_st.stddev[0]) < 12.0
                if (top_dark or bot_dark) and float(mid_st.mean[0]) > 35.0:
                    if auto_sanitize and width >= 540 and height >= 540:
                        if top_dark and bot_dark:
                            lb_vf = "crop=iw:ih*0.64:0:ih*0.18,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
                        elif top_dark:
                            lb_vf = "crop=iw:ih*0.76:0:ih*0.20,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
                        else:
                            lb_vf = "crop=iw:ih*0.76:0:ih*0.04,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
                        tmp_lb = str(p.with_name(f"{p.stem}_nolb{p.suffix}"))
                        subprocess.run(
                            [
                                "ffmpeg", "-v", "error", "-y",
                                "-i", str(p),
                                "-vf", lb_vf,
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an",
                                tmp_lb,
                            ],
                            capture_output=True,
                            timeout=18,
                            check=False,
                        )
                        if Path(tmp_lb).exists() and Path(tmp_lb).stat().st_size > 15000:
                            shutil.move(tmp_lb, str(p))
                            logger.info(
                                f"✂️ [Letterbox Sanitizer] Đã tự động cắt bỏ dải viền đen ngang "
                                f"(top_dark={top_dark}, bot_dark={bot_dark}) khỏi '{p.name}'!"
                            )
                            return cls.inspect_clip_before_concat(
                                video_path=str(p),
                                min_short_edge=min_short_edge,
                                min_sharpness=min_sharpness,
                                check_watermark=check_watermark,
                                allow_snow=allow_snow,
                                auto_sanitize=False,
                            )
                        Path(tmp_lb).unlink(missing_ok=True)
                    issues.append(f"Clip chứa dải viền đen letterbox ở đỉnh/đáy (top_dark={top_dark}, bot_dark={bot_dark})")
            except Exception:
                pass

        # Kiểm tra đứng hình / freeze-frame / ảnh tĩnh giả video ngay trên các khung hình mẫu:
        # Video chuyển động thật có pair_diffs ~ 15.0 - 50.0; clip ảnh tĩnh/slideshow có pair_diffs < 6.5
        if len(gray_frames) >= 3:
            try:
                pair_diffs = [
                    float(ImageStat.Stat(ImageChops.difference(gray_frames[i], gray_frames[i + 1])).mean[0])
                    for i in range(len(gray_frames) - 1)
                ]
                avg_m_diff = sum(pair_diffs) / len(pair_diffs) if pair_diffs else 0.0
                if pair_diffs and (min(pair_diffs) < 4.5 or avg_m_diff < 6.5):
                    issues.append(
                        f"Clip bị đứng hình hoặc là ảnh tĩnh giả video (min_motion_diff={min(pair_diffs):.2f}, avg={avg_m_diff:.2f} < 6.5)"
                    )
            except Exception:
                pass

        # Kiểm tra logo/watermark cố định ở 4 góc hoặc banner chữ khi cảnh nền chuyển động
        detected_corner_zone: Optional[Tuple[str, Tuple[int, int, int, int]]] = None
        center_is_clean: bool = True
        if check_watermark and len(edge_frames) >= 2 and len(gray_frames) >= 2:
            try:
                diff_01 = float(ImageStat.Stat(ImageChops.difference(gray_frames[0], gray_frames[-1])).mean[0])
                if diff_01 >= 3.5:
                    w_s, h_s = 180, 320
                    e_lists = [list(ef.getdata()) for ef in edge_frames]
                    pers_e = [min(vals) for vals in zip(*e_lists)]
                    pers_im = Image.new("L", (w_s, h_s))
                    pers_im.putdata(pers_e)

                    center_box = pers_im.crop((36, 96, 144, 224))
                    center_mean = max(float(ImageStat.Stat(center_box).mean[0]), 4.0)
                    center_is_clean = center_mean < 28.0

                    corner_boxes = [
                        ("top_left", (4, 6, 68, 54)),
                        ("top_right", (112, 6, 176, 54)),
                        ("bottom_left", (4, 256, 68, 308)),
                        ("bottom_right", (112, 256, 176, 308)),
                        ("top_banner", (22, 10, 158, 52)),
                        ("bottom_banner", (18, 258, 162, 314)),
                    ]
                    for c_name, box in corner_boxes:
                        c_diff = float(
                            ImageStat.Stat(
                                ImageChops.difference(gray_frames[0].crop(box), gray_frames[-1].crop(box))
                            ).mean[0]
                        )
                        # Chỉ coi vùng góc có cạnh tĩnh (pers_im) là logo đóng cứng khi cảnh nền tại chính góc đó CÓ chuyển động (c_diff >= 11.5),
                        # tránh báo nhầm cành cây rừng/tảng đá đứng yên khi máy quay đặt trên chân máy (tripod)!
                        if c_diff < 11.5:
                            continue
                        c_mean = float(ImageStat.Stat(pers_im.crop(box)).mean[0])
                        min_c_thr = 28.0 if "banner" in c_name else 36.0
                        mult_thr = 1.90 if "banner" in c_name else 2.25
                        if c_mean >= min_c_thr and c_mean >= center_mean * mult_thr:
                            detected_corner_zone = (c_name, box)
                            break
            except Exception:
                pass

        # ── QUY TRÌNH 3 BƯỚC XỬ LÝ TAG / LOGO BẢN QUYỀN: (1) CẮT NÉ -> (2) XÓA DELOGO/CROP -> (3) BỎ ──
        has_watermark_issue = any(frame_bad_flags) or (detected_corner_zone is not None)
        if auto_sanitize and check_watermark and has_watermark_issue and len(issues) == 0 and width >= 540 and height >= 540:
            # BƯỚC 1: Cắt né qua đoạn đầu (Intro) hoặc đoạn cuối (Outro) bị dính tag nếu đoạn giữa (frame 1) SẠCH
            if (
                len(frame_bad_flags) == 3
                and not frame_bad_flags[1]
                and (frame_bad_flags[0] or frame_bad_flags[2])
                and detected_corner_zone is None
                and dur >= 3.8
            ):
                clean_ss = round(dur * 0.22, 2) if frame_bad_flags[0] else 0.0
                clean_end = round(dur * 0.78, 2) if frame_bad_flags[2] else dur
                clean_len = round(clean_end - clean_ss, 2)
                if clean_len >= 2.4:
                    tmp_skip = str(p.with_name(f"{p.stem}_step1_skip{p.suffix}"))
                    cmd_skip = [
                        "ffmpeg", "-v", "error", "-y",
                        "-ss", f"{clean_ss:.2f}",
                        "-t", f"{clean_len:.2f}",
                        "-i", str(p),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an",
                        tmp_skip,
                    ]
                    subprocess.run(cmd_skip, capture_output=True, timeout=18, check=False)
                    if Path(tmp_skip).exists() and Path(tmp_skip).stat().st_size > 15000:
                        shutil.move(tmp_skip, str(p))
                        logger.info(
                            f"✂️ [Watermark Step 1 - Cắt Né] Đã cắt bỏ đoạn Intro/Outro dính tag cho '{p.name}' "
                            f"(lấy đoạn sạch {clean_ss:.1f}s -> {clean_end:.1f}s)"
                        )
                        return cls.inspect_clip_before_concat(
                            video_path=str(p),
                            min_short_edge=min_short_edge,
                            min_sharpness=min_sharpness,
                            check_watermark=check_watermark,
                            allow_snow=allow_snow,
                            auto_sanitize=False,
                        )
                    Path(tmp_skip).unlink(missing_ok=True)

            # BƯỚC 2: Xóa logo bằng bộ lọc FFmpeg `delogo` kết hợp phóng Crop dịch tâm né góc chứa logo / caption
            if center_is_clean and (detected_corner_zone is not None or any(frame_bad_flags)):
                default_zone = ("top_banner", (18, 6, 162, 50))
                if detected_corner_zone is None and bad_art_reasons:
                    r0 = bad_art_reasons[0]
                    m_bt = re.search(r"bot=(\d+),top=(\d+)", r0) if "re" in dir() else None
                    if m_bt is None:
                        import re as _re
                        m_bt = _re.search(r"bot=(\d+),top=(\d+)", r0)
                    if m_bt and int(m_bt.group(1)) >= int(m_bt.group(2)):
                        default_zone = ("bottom_banner", (18, 252, 162, 312))
                z_name, (bx1, by1, bx2, by2) = detected_corner_zone or default_zone
                lx = max(2, int(bx1 * width / 180.0))
                ly = max(2, int(by1 * height / 320.0))
                lw = max(16, min(width - lx - 4, int((bx2 - bx1) * width / 180.0)))
                lh = max(16, min(height - ly - 4, int((by2 - by1) * height / 320.0)))

                # Phóng 120% và dịch tâm crop ngược hướng với góc/dải có logo/caption để vừa xóa vừa cắt bay vùng rìa
                zw = (int(width * 1.20) // 2) * 2
                zh = (int(height * 1.20) // 2) * 2
                x_expr = "0" if "right" in z_name else ("(iw-ow)" if "left" in z_name else "(iw-ow)/2")
                y_expr = "0" if "bottom" in z_name else ("(ih-oh)" if "top" in z_name else "(ih-oh)/2")

                vf_delogo = (
                    f"delogo=x={lx}:y={ly}:w={lw}:h={lh},"
                    f"scale={zw}:{zh}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}:{x_expr}:{y_expr}"
                )
                tmp_delogo = str(p.with_name(f"{p.stem}_step2_delogo{p.suffix}"))
                cmd_delogo = [
                    "ffmpeg", "-v", "error", "-y",
                    "-i", str(p),
                    "-vf", vf_delogo,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an",
                    tmp_delogo,
                ]
                subprocess.run(cmd_delogo, capture_output=True, timeout=22, check=False)
                if Path(tmp_delogo).exists() and Path(tmp_delogo).stat().st_size > 15000:
                    recheck = cls.inspect_clip_before_concat(
                        video_path=tmp_delogo,
                        min_short_edge=min_short_edge,
                        min_sharpness=min_sharpness,
                        check_watermark=check_watermark,
                        allow_snow=allow_snow,
                        auto_sanitize=False,
                    )
                    if recheck.get("passed", False):
                        shutil.move(tmp_delogo, str(p))
                        logger.info(
                            f"🧼 [Watermark Step 2 - Xóa Delogo & Crop] Đã xóa sạch logo vùng '{z_name}' "
                            f"(delogo={lx},{ly},{lw}x{lh} + crop 116%) cho '{p.name}'!"
                        )
                        return recheck
                    Path(tmp_delogo).unlink(missing_ok=True)

        # BƯỚC 3: Nếu không cứu được (hoặc đã qua Bước 1 & 2 mà vẫn còn chữ đóng cứng giữa màn hình) -> Báo lỗi để BỎ!
        if any(frame_bad_flags):
            reason_str = bad_art_reasons[0] if bad_art_reasons else "embedded text/logo"
            issues.append(f"Phát hiện chữ/logo/khung ghép đóng cứng không thể xóa ({reason_str})")
        if detected_corner_zone is not None:
            c_name, _ = detected_corner_zone
            issues.append(f"Phát hiện watermark/logo kênh khác đóng cứng tại vùng {c_name}")

        passed = len(issues) == 0
        base_score = min(9.8, 7.6 + max(0.0, avg_sharp - min_sharpness) * 0.22)
        if not passed:
            base_score = max(2.5, base_score - 3.5 * len(issues))

        return {
            "passed": passed,
            "score": round(base_score, 1),
            "issues": issues,
            "width": width,
            "height": height,
            "sharpness": round(avg_sharp, 2),
            "brightness": round(avg_lum, 1),
        }

    @classmethod
    def _gemini_vision_audit_contact_sheet(
        cls,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
    ) -> Dict[int, str]:
        """
        Trích xuất khung hình đại diện của toàn bộ các phân cảnh, ghép thành 1 bảng Contact Sheet
        có đánh số `S1, S2, ...` và gửi cho Gemini Vision kiểm tra trực quan:
        - Phát hiện ảnh/video chứa chữ giải thích tạp chí/infographic ('HOW IT WORKS', sơ đồ mũi tên, watermark lớn).
        - Phát hiện cảnh lệch hoàn toàn chủ đề/bối cảnh (VD: bàn DJ âm thanh, rừng cây ban ngày, xe hơi trên tuyết khi đang nói về thủy quái biển sâu, hoặc nhầm loài vật như chồn hôi/báo đốm khi nói về lửng mật).
        Trả về dict `{scene_id: lý_do_loại}`.
        """
        import base64
        import io
        import json
        import re
        import subprocess
        import tempfile
        import requests
        from PIL import ImageDraw
        from config.settings import settings
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        if not scenes_media or not primary_entity:
            return {}
        keys = settings.get_gemini_keys_pool()
        if not keys:
            return {}

        tw, th = 220, 390
        thumbs: List[tuple[int, Image.Image, str]] = []
        with tempfile.TemporaryDirectory() as td:
            for item in scenes_media:
                sid = int(item.get("scene_id") or 0)
                mpath = str(item.get("media_path") or "")
                prod_m_cs = str(item.get("production_method") or "")
                narr = str(item.get("narration") or item.get("visual_keyword") or "")
                if sid <= 0 or not mpath or not Path(mpath).exists():
                    continue
                if (
                    "DOODLE_2D" in prod_m_cs
                    or "16X9" in prod_m_cs
                    or mpath.lower().endswith(("_doodle_2d.mp4", "_doodle.mp4", "_16x9.mp4"))
                ):
                    continue
                p = Path(mpath)
                im_obj: Optional[Image.Image] = None
                try:
                    if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
                        f_jpg = str(Path(td) / f"s_{sid}.jpg")
                        subprocess.run(
                            [
                                "ffmpeg", "-v", "error", "-y",
                                "-ss", "1.0", "-i", str(p),
                                "-frames:v", "1", "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}",
                                f_jpg,
                            ],
                            capture_output=True,
                            timeout=8,
                        )
                        if Path(f_jpg).exists() and Path(f_jpg).stat().st_size > 1000:
                            with Image.open(f_jpg) as im_tmp:
                                im_obj = im_tmp.convert("RGB").copy()
                    else:
                        with Image.open(str(p)) as im_tmp:
                            im_rgb = im_tmp.convert("RGB")
                            im_obj = im_rgb.resize((tw, th), Image.Resampling.BILINEAR)
                except Exception:
                    im_obj = None

                if im_obj is not None:
                    draw = ImageDraw.Draw(im_obj)
                    draw.rectangle([(0, 0), (96, 30)], fill=(0, 0, 0))
                    draw.text((8, 7), f"SCENE #{sid}", fill=(255, 230, 0))
                    thumbs.append((sid, im_obj, narr[:90]))

        if not thumbs:
            return {}

        cols = min(4, len(thumbs))
        rows = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * th), (12, 12, 12))
        scene_lines: List[str] = []
        for idx, (sid, im_t, narr_t) in enumerate(thumbs):
            r_i = idx // cols
            c_i = idx % cols
            sheet.paste(im_t, (c_i * tw, r_i * th))
            scene_lines.append(f"- SCENE #{sid}: \"{narr_t}\"")

        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=78)
        b64_sheet = base64.b64encode(buf.getvalue()).decode("ascii")

        prompt = f"""You are a strict Visual Continuity & Quality Director inspecting the final scene frames of a YouTube Short BEFORE rendering.
Main Subject / Entity of this video: "{primary_entity}"
Scenes in the attached contact sheet (labeled SCENE #1, SCENE #2, ...):
{chr(10).join(scene_lines)}

Inspect every labeled tile carefully and flag ONLY scenes that have severe visual defects:
1. Magazine / Textbook / Infographic clutter: Frames containing paragraphs of explanatory text, anatomy callout arrows/labels, or big magazine logos (e.g. "HOW IT WORKS").
2. Completely wrong species or absurdly off-topic environment:
   - e.g. Showing a striped skunk or leopard when the subject is specifically a Honey Badger.
   - e.g. Showing a purple DJ audio mixer board, a person lying in a green forest, or a car on a snowy highway when the video is about a deep-sea monster ("{primary_entity}") in the ocean trench.

Return ONLY valid JSON in this exact format:
{{
  "failed_scenes": [
    {{"scene_id": 4, "reason": "Contains magazine infographic text labels and HOW IT WORKS watermark"}}
  ]
}}"""

        rotator = KeyRotator(settings.get_gemini_keys_pool())
        vision_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
        for model_name, api_key in rotator.iter_best_model_key_pairs(vision_models, max_total_attempts=3):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_sheet}},
                        ]
                    }
                ],
                "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1),
            }
            try:
                resp = requests.post(url, json=payload, timeout=KeyRotator.fast_timeout(14))
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            out_reasons: Dict[int, str] = {}
                            for item_f in (data.get("failed_scenes") or []):
                                if isinstance(item_f, dict) and item_f.get("scene_id"):
                                    out_reasons[int(item_f["scene_id"])] = str(item_f.get("reason") or "Lệch chủ đề/chứa chữ rác")
                            return out_reasons
                elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
            except Exception as e_v:
                rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(e_v))
                continue
        return {}

    @trace_custom_span("VisualCritic.evaluate_media", run_type="chain")
    def evaluate_media(
        self,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None
    ) -> VisualEvaluationReport:
        """
        Kiểm định SONG SONG ĐỘ SẮC NÉT, ĐỘ PHÂN GIẢI, TÍNH TOÀN VẸN BITMAP & CHỐNG TRÙNG LẶP của tất cả các cảnh.
        Sử dụng ThreadPoolExecutor để kiểm tra đồng loạt toàn bộ các cảnh cùng lúc.
        """
        from concurrent.futures import ThreadPoolExecutor

        def _inspect_single_scene(item: Dict[str, Any]) -> Dict[str, Any]:
            scene_id = item.get("scene_id", 0)
            media_path = item.get("media_path", "")
            media_type = item.get("media_type", "image")
            p = Path(media_path)

            issues: List[str] = []
            rel_score = 9.5
            entity_score = 9.5
            aes_score = 9.5
            valid_bitmap = True
            img_hash = ""
            sig: List[str] = []

            if not p.exists() or p.stat().st_size < 2048:
                return {
                    "scene_id": scene_id,
                    "media_path": media_path,
                    "rel_score": 1.0,
                    "entity_score": 1.0,
                    "aes_score": 1.0,
                    "valid_bitmap": False,
                    "issues": ["File media không tồn tại hoặc dung lượng rỗng (< 2KB)"],
                    "failed": True,
                    "img_hash": "",
                    "sig": [],
                }

            failed = False
            prod_m_raw = str(item.get("production_method") or "")
            is_doodle_clip = (
                "DOODLE_2D" in prod_m_raw
                or p.name.lower().endswith(("_doodle_2d.mp4", "_doodle.mp4"))
            )
            is_longform_chapter_clip = (
                prod_m_raw.startswith("LONGFORM_")
                or p.name.lower().endswith("_16x9.mp4")
            )
            is_long_peak_cont = (
                prod_m_raw == "LONG_PEAK_CONTINUOUS_FLOW"
                or is_doodle_clip
                or is_longform_chapter_clip
            )
            if media_type == "image":
                # Kiểm tra & lọc sạch danh sách pose_images (nếu có) để loại bỏ file phụ lỗi/đen/vỡ hạt/dính chữ trước khi dựng
                raw_poses = item.get("pose_images")
                if isinstance(raw_poses, list) and len(raw_poses) > 1:
                    clean_poses: List[str] = []
                    from app.services.video_memory_rag import VideoMemoryRAG
                    for pp in raw_poses:
                        if not pp or not Path(pp).exists() or Path(pp).stat().st_size < 2500:
                            continue
                        if self._is_solid_color_or_black(str(pp)):
                            continue
                        try:
                            with Image.open(str(pp)) as im_p:
                                wp, hp = im_p.size
                            if min(wp, hp) < 540:
                                continue
                            has_bad_p, _ = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(Path(pp).read_bytes())
                            if has_bad_p:
                                continue
                            if self._measure_sharpness(str(pp)) < 5.8:
                                continue
                        except Exception:
                            continue
                        clean_poses.append(str(pp))
                    if clean_poses:
                        item["pose_images"] = clean_poses
                        if str(p) not in clean_poses:
                            media_path = clean_poses[0]
                            item["media_path"] = media_path
                            p = Path(media_path)

                try:
                    with Image.open(str(p)) as im:
                        im.verify()
                    with Image.open(str(p)) as im:
                        w, h = im.size
                        if min(w, h) < 540:
                            aes_score -= 3.5
                            issues.append(f"Độ phân giải quá thấp (kém sắc nét): {w}x{h} px (yêu cầu >= 540px)")
                            failed = True
                        aspect = max(w / h, h / w)
                        if aspect > 2.6:
                            aes_score -= 4.0
                            issues.append(f"Tỷ lệ khung hình bất thường (dải hẹp/banner: {w}x{h})")
                            failed = True

                    if self._is_solid_color_or_black(str(p)):
                        aes_score = 2.0
                        issues.append("Ảnh bị tối đen hoàn toàn hoặc đơn sắc lỗi")
                        failed = True
                    else:
                        try:
                            from app.services.video_memory_rag import VideoMemoryRAG
                            has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(p.read_bytes())
                            if has_bad_art:
                                aes_score -= 4.5
                                issues.append(f"Ảnh chứa chữ/logo/khung ghép lỗi ({art_reason})")
                                failed = True
                        except Exception:
                            pass
                        sharpness = self._measure_sharpness(str(p))
                        min_req_sharpness = 12.0 if scene_id == 1 else 5.8
                        if sharpness < min_req_sharpness:
                            aes_score = max(3.5, round((sharpness * 0.52) if scene_id == 1 else (sharpness * 0.9), 1))
                            issues.append(f"Ảnh bị mờ nhòe / thiếu độ sắc nét (sharpness={sharpness:.1f} < {min_req_sharpness:.1f})")
                            failed = True
                        elif not failed:
                            aes_score = round(min(9.8, 7.6 + (sharpness - min_req_sharpness) * 0.22), 1)
                        img_hash = self._compute_image_hash(str(p))
                        if img_hash:
                            sig = [img_hash]
                except Exception as ex:
                    valid_bitmap = False
                    issues.append(f"Lỗi đọc file ảnh với Pillow: {ex}")
                    failed = True
            else:
                try:
                    sig = self._media_signature(str(p))
                    prod_m = str(item.get("production_method") or "")
                    is_pregraded_hook = (
                        media_type == "hook_ready_video"
                        or is_doodle_clip
                        or is_longform_chapter_clip
                        or prod_m in (
                            "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK",
                            "CLIMAX_PEAK_SHORT_SPLICE",
                            "MONSTER_3D_CGI_VIDEO_PEAK",
                            "CLIMAX_STOCK_VIDEO_REAL_MOTION",
                            "TOOL_DOODLE_2D_EXPLAINER",
                            "REFINED_CLEAN_MOTION_RESCUE",
                            "FINAL_GATE_CLEAN_VIDEO_RESCUE",
                        )
                        or p.name.lower().endswith(("_doodle_2d.mp4", "_doodle.mp4", "_16x9.mp4"))
                    )
                    if is_doodle_clip:
                        aes_score = 9.4
                    elif scene_id == 1:
                        from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                        ok_s1_sh, s1_sh_val = _ensure_hook_clip_sharpness_and_luma(str(p), min_sharpness=12.0)
                        if not ok_s1_sh:
                            aes_score = max(3.8, round(s1_sh_val * 0.5, 1))
                            issues.append(
                                f"Khung hình mở đầu (Cảnh 1) chưa đạt chuẩn độ sắc nét (sharpness={s1_sh_val:.2f} < 12.0)"
                            )
                            failed = True
                        elif is_pregraded_hook:
                            aes_score = 9.2
                        else:
                            clip_gate = self.inspect_clip_before_concat(
                                str(p),
                                min_short_edge=720,
                                min_sharpness=12.0,
                                check_watermark=True,
                            )
                            aes_score = float(clip_gate.get("score", 8.0))
                            if not clip_gate.get("passed", True):
                                issues.extend(clip_gate.get("issues") or ["Clip Cảnh 1 không đạt cổng kiểm duyệt chất lượng/watermark"])
                                failed = True
                    elif is_pregraded_hook:
                        # Clip thân đã vượt qua kiểm duyệt gốc & Gemini Vision trước khi phủ LUT màu tối (niche grading)
                        aes_score = 9.2
                    else:
                        clip_gate = self.inspect_clip_before_concat(
                            str(p),
                            min_short_edge=720,
                            min_sharpness=5.8,
                            check_watermark=True,
                        )
                        aes_score = float(clip_gate.get("score", 8.0))
                        if not clip_gate.get("passed", True):
                            issues.extend(clip_gate.get("issues") or ["Clip không đạt cổng kiểm duyệt chất lượng/watermark"])
                            failed = True
                except Exception as ex_sig:
                    logger.debug(f"[VisualCritic] Lỗi kiểm tra khung hình video cảnh {scene_id}: {ex_sig}")

            return {
                "scene_id": scene_id,
                "media_path": media_path,
                "rel_score": rel_score,
                "entity_score": entity_score,
                "aes_score": aes_score,
                "valid_bitmap": valid_bitmap,
                "issues": issues,
                "failed": failed,
                "img_hash": img_hash,
                "sig": sig,
                "is_long_peak_cont": is_long_peak_cont,
            }

        max_workers = min(max(len(scenes_media), 1), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            inspected_list = list(pool.map(_inspect_single_scene, scenes_media))

        vision_semantic_failures: Dict[int, str] = {}
        try:
            vision_semantic_failures = self._gemini_vision_audit_contact_sheet(
                scenes_media=scenes_media,
                primary_entity=primary_entity,
            )
            if vision_semantic_failures:
                logger.warning(
                    f"👁️ [VisualCritic ContactSheet Vision] Phát hiện {len(vision_semantic_failures)} cảnh lệch nội dung/chứa chữ tạp chí: "
                    f"{vision_semantic_failures}"
                )
        except Exception as e_cs:
            logger.debug(f"[VisualCritic ContactSheet Vision] skip: {e_cs}")

        scene_scores: List[SceneVisualScore] = []
        failed_scene_ids: List[int] = []
        seen_hashes: Dict[str, int] = {}
        seen_signatures: Dict[int, List[str]] = {}
        issues_summary: List[str] = []

        for res in inspected_list:
            scene_id = res["scene_id"]
            issues = list(res["issues"])
            aes_score = res["aes_score"]
            failed = res["failed"]
            img_hash = res["img_hash"]
            sig = res["sig"]
            is_long_peak_cont = bool(res.get("is_long_peak_cont"))

            if scene_id in vision_semantic_failures:
                v_reason = vision_semantic_failures[scene_id]
                issues.append(f"Gemini Vision ContactSheet: {v_reason}")
                res["rel_score"] = 4.0
                res["entity_score"] = 4.0
                aes_score = min(aes_score, 4.5)
                failed = True

            if img_hash:
                if img_hash in seen_hashes:
                    prev_id = seen_hashes[img_hash]
                    issues.append(f"Ảnh trùng lặp 100% với Cảnh #{prev_id}")
                    aes_score -= 4.0
                    failed = True
                else:
                    seen_hashes[img_hash] = scene_id

            if sig:
                dup_id = None if is_long_peak_cont else self._find_duplicate(sig, seen_signatures)
                if dup_id is not None and not any("trùng lặp" in i for i in issues):
                    issues.append(f"Hình ảnh/clip trùng lặp với Cảnh #{dup_id}")
                    aes_score -= 4.0
                    failed = True
                    logger.warning(f"🔁 [VisualCritic] Cảnh {scene_id} trùng hình ảnh với Cảnh #{dup_id} → sẽ thay thế")
                else:
                    seen_signatures[scene_id] = sig

            if failed:
                failed_scene_ids.append(scene_id)
            if issues:
                issues_summary.append(f"Cảnh {scene_id}: {'; '.join(issues)}")

            scene_scores.append(SceneVisualScore(
                scene_id=scene_id,
                media_path=res["media_path"],
                relevance_score=max(1.0, res["rel_score"]),
                entity_match_score=max(1.0, res["entity_score"]),
                aesthetic_score=max(1.0, aes_score),
                is_valid_bitmap=res["valid_bitmap"],
                issues=issues,
            ))

        total_scenes = len(scene_scores)
        overall = sum(s.aesthetic_score for s in scene_scores) / total_scenes if total_scenes > 0 else 0.0
        passed = len(failed_scene_ids) == 0 and overall >= 7.0

        return VisualEvaluationReport(
            scene_scores=scene_scores,
            overall_score=round(overall, 1),
            failed_scene_ids=list(set(failed_scene_ids)),
            passed=passed,
            summary="\n".join(issues_summary) if issues_summary else "Tất cả hình ảnh/video đạt chuẩn độ sắc nét & độ phân giải."
        )
