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
import re
import time
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
                top_subject_mean = sum(tile_means[:14]) / 14.0 if tile_means else whole_mean
                return round(max(whole_mean, top_subject_mean * 0.72), 2)
        except Exception:
            return 10.0

    # Ngưỡng Hamming (trên 64 bit) để coi 2 khung hình là "cùng 1 cảnh quay" dù bị nén/scale/crop/LUT khác nhau
    _DUP_HAMMING = 8
    _DUP_HAMMING_MULTI = 11

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
        if not p.exists():
            return []
        try:
            st_sig = p.stat()
            sig_ck = (str(p.resolve()), st_sig.st_mtime_ns, st_sig.st_size)
            cached_sig = getattr(cls, "_media_sig_cache", {}).get(sig_ck)
            if cached_sig is not None:
                return list(cached_sig)
        except Exception:
            sig_ck = None

        if p.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
            if cls._is_solid_color_or_black(str(p)):
                return []
            h = cls._compute_image_hash(str(p))
            res_img_sig = [h] if h else []
            if sig_ck is not None:
                if not hasattr(cls, "_media_sig_cache"):
                    cls._media_sig_cache = {}
                cls._media_sig_cache[sig_ck] = list(res_img_sig)
            return res_img_sig

        sig: List[str] = []
        try:
            from app.utils.ffmpeg_helper import get_duration_ms
            dur = max(get_duration_ms(str(p)) / 1000.0, 0.8)
        except Exception:
            dur = 3.0
        with tempfile.TemporaryDirectory() as td:
            fps_val = max(0.8, 5.0 / max(dur, 1.0))
            out_pattern = str(Path(td) / "f_%02d.jpg")
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-y", "-i", str(p),
                        "-vf", f"fps={fps_val:.3f},scale=160:-1",
                        "-frames:v", "5", out_pattern,
                    ],
                    capture_output=True, timeout=12,
                )
                for frame_p in sorted(Path(td).glob("f_*.jpg")):
                    if frame_p.exists() and not cls._is_solid_color_or_black(str(frame_p)):
                        h = cls._compute_image_hash(str(frame_p))
                        if h:
                            sig.append(h)
            except Exception:
                pass
        if sig_ck is not None:
            if not hasattr(cls, "_media_sig_cache"):
                cls._media_sig_cache = {}
            cls._media_sig_cache[sig_ck] = list(sig)
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

    _clip_inspect_cache: Dict[Tuple[str, int, int, int, float, bool], Dict[str, Any]] = {}

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
        try:
            st_info = p.stat()
            cache_key = (str(p.resolve()), st_info.st_mtime_ns, st_info.st_size, min_short_edge, round(min_sharpness, 1), check_watermark)
            cached_res = cls._clip_inspect_cache.get(cache_key)
            if cached_res is not None:
                return dict(cached_res)
        except Exception:
            cache_key = None

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
                        sharp_vals.append(cls._measure_sharpness(f_jpg))

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
        if min_con < 13.0:
            issues.append(f"Clip thiếu tương phản hoặc phẳng màu (contrast={min_con:.1f} < 13)")
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
                scan_frames_lb = gray_frames[:min(4, len(gray_frames))]
                gf0 = gray_frames[0]
                top_st = ImageStat.Stat(gf0.crop((0, 0, 180, 24)))
                bot_st = ImageStat.Stat(gf0.crop((0, 296, 180, 320)))
                mid_st = ImageStat.Stat(gf0.crop((0, 80, 180, 240)))
                top_cand = float(top_st.mean[0]) < 16.0 and float(top_st.stddev[0]) < 12.0
                bot_cand = float(bot_st.mean[0]) < 16.0 and float(bot_st.stddev[0]) < 12.0
                if top_cand and bot_cand:
                    # Letterbox đối xứng 2 đầu (top + bottom)
                    top_dark = True
                    bot_dark = True
                else:
                    # Dải đen đơn lẻ 1 đầu: Chỉ bắt khi là dải đen kỹ thuật số phẳng tuyệt đối (mean < 8.5, stddev < 3.5)
                    # trên toàn bộ các khung hình mẫu; tuyệt đối không bắt nhầm áo đen / bóng đổ tự nhiên / vignette!
                    top_dark = bool(
                        top_cand
                        and all(
                            float(ImageStat.Stat(gf.crop((0, 0, 180, 20))).mean[0]) < 8.5
                            and float(ImageStat.Stat(gf.crop((0, 0, 180, 20))).stddev[0]) < 3.5
                            for gf in scan_frames_lb
                        )
                    )
                    bot_dark = bool(
                        bot_cand
                        and all(
                            float(ImageStat.Stat(gf.crop((0, 300, 180, 320))).mean[0]) < 8.5
                            and float(ImageStat.Stat(gf.crop((0, 300, 180, 320))).stddev[0]) < 3.5
                            for gf in scan_frames_lb
                        )
                    )
                if (top_dark or bot_dark) and float(mid_st.mean[0]) > 35.0:
                    if auto_sanitize and width >= 540 and height >= 540:
                        # Đo chính xác ranh giới dải viền đen trên tất cả các khung hình mẫu (không đoán tỷ lệ cố định 18%/20%)
                        scan_frames = gray_frames[:min(4, len(gray_frames))]
                        y_top_bar = 0
                        if top_dark:
                            for ry in range(0, 128, 2):
                                band_means = [float(ImageStat.Stat(gf.crop((0, ry, 180, ry + 2))).mean[0]) for gf in scan_frames]
                                band_stds = [float(ImageStat.Stat(gf.crop((0, ry, 180, ry + 2))).stddev[0]) for gf in scan_frames]
                                if max(band_means) < 20.0 and max(band_stds) < 15.0:
                                    y_top_bar = ry + 2
                                else:
                                    break
                            y_top_bar = min(132, max(y_top_bar, 26) + 6)
                        y_bot_bar = 320
                        if bot_dark:
                            for ry in range(318, 190, -2):
                                band_means = [float(ImageStat.Stat(gf.crop((0, ry, 180, ry + 2))).mean[0]) for gf in scan_frames]
                                band_stds = [float(ImageStat.Stat(gf.crop((0, ry, 180, ry + 2))).stddev[0]) for gf in scan_frames]
                                if max(band_means) < 20.0 and max(band_stds) < 15.0:
                                    y_bot_bar = ry
                                else:
                                    break
                            y_bot_bar = max(188, min(y_bot_bar, 294) - 6)

                        clean_rows = y_bot_bar - y_top_bar
                        # Chỉ cứu khi vùng hình thực chiếm >= 48% chiều cao khung dọc (>= 154/320 hàng);
                        # nếu là dải ngang 16:9 quá hẹp nhét trong nền đen 9:16 (< 48%), bỏ qua ngay để tránh phóng vỡ hình!
                        if clean_rows >= 154:
                            y_ratio = round(y_top_bar / 320.0, 4)
                            h_ratio = round(clean_rows / 320.0, 4)
                            lb_vf = (
                                f"crop=iw:ih*{h_ratio}:0:ih*{y_ratio},"
                                "scale=1080:1920:force_original_aspect_ratio=increase,"
                                "crop=1080:1920,setsar=1"
                            )
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
                                recheck_lb = cls.inspect_clip_before_concat(
                                    video_path=tmp_lb,
                                    min_short_edge=min_short_edge,
                                    min_sharpness=min_sharpness,
                                    check_watermark=check_watermark,
                                    allow_snow=allow_snow,
                                    auto_sanitize=False,
                                )
                                has_residual_lb = any("letterbox" in str(iss).lower() for iss in (recheck_lb.get("issues") or []))
                                if not has_residual_lb:
                                    shutil.move(tmp_lb, str(p))
                                    logger.info(
                                        f"✂️ [Letterbox Sanitizer] Đã tự động cắt sạch dải viền đen ngang "
                                        f"(y={y_ratio:.2f}, h={h_ratio:.2f}) khỏi '{p.name}'!"
                                    )
                                    return recheck_lb
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
                if pair_diffs and (avg_m_diff < 0.75 and max(pair_diffs) < 1.1):
                    issues.append(
                        f"Clip bị đứng hình hoặc là ảnh tĩnh giả video (min_motion_diff={min(pair_diffs):.2f}, avg={avg_m_diff:.2f} < 0.75)"
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
                        ("bottom_right_micro", (148, 286, 179, 319)),
                        ("bottom_left_micro", (1, 286, 32, 319)),
                        ("top_right_micro", (148, 2, 179, 34)),
                        ("top_left_micro", (1, 2, 32, 34)),
                        ("top_banner", (22, 10, 158, 52)),
                        ("bottom_banner", (18, 258, 162, 314)),
                    ]
                    for c_name, box in corner_boxes:
                        c_diff = float(
                            ImageStat.Stat(
                                ImageChops.difference(gray_frames[0].crop(box), gray_frames[-1].crop(box))
                            ).mean[0]
                        )
                        is_micro = c_name.endswith("_micro")
                        # Chỉ coi vùng góc có cạnh tĩnh (pers_im) là logo đóng cứng khi cảnh nền có chuyển động,
                        # tránh báo nhầm cành cây rừng/tảng đá đứng yên khi máy quay đặt trên chân máy (tripod)!
                        if c_diff < (7.5 if is_micro else 11.5):
                            continue
                        c_mean = float(ImageStat.Stat(pers_im.crop(box)).mean[0])
                        min_c_thr = 22.0 if is_micro else (28.0 if "banner" in c_name else 36.0)
                        mult_thr = 1.75 if is_micro else (1.90 if "banner" in c_name else 2.25)
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
                    m_bt = re.search(r"bot=(\d+),top=(\d+)", r0)
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

        out_dict = {
            "passed": passed,
            "score": round(base_score, 1),
            "issues": issues,
            "width": width,
            "height": height,
            "sharpness": round(avg_sharp, 2),
            "brightness": round(avg_lum, 1),
        }
        try:
            if p.exists():
                st_now = p.stat()
                ck_now = (str(p.resolve()), st_now.st_mtime_ns, st_now.st_size, min_short_edge, round(min_sharpness, 1), check_watermark)
                cls._clip_inspect_cache[ck_now] = dict(out_dict)
        except Exception:
            pass
        return out_dict

    @classmethod
    def _gemini_vision_audit_contact_sheet(
        cls,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        channel_style: Optional[str] = None,
    ) -> Dict[int, Dict[str, Any]]:
        """
        Trích xuất khung hình đại diện của toàn bộ các phân cảnh, ghép thành 1 bảng Contact Sheet
        có đánh số `SCENE #1, SCENE #2, ...` và gửi cho Gemini Vision tự chấm điểm 3 trục cho từng cảnh:
        - `entity_match_score` (0.0–10.0): Khớp đúng Thực thể Đích danh của kịch bản (sau khi quy chiếu đại từ/danh từ chung trong câu thoại về Topic).
        - `relevance_score` (0.0–10.0): Khớp hành động, trạng thái vật lý và bối cảnh của phân cảnh.
        - `cleanliness_score` (0.0–10.0): Không chứa chữ tạp chí/infographic, watermark, viền đen hoặc bối cảnh nhân tạo lệch ngữ cảnh.
        - `suggested_search_query`: Truy vấn tiếng Anh 2–4 từ gợi ý sửa chữa khi cảnh không đạt (`< 7.5`).
        Trả về dict `{scene_id: {"passed": bool, "entity_match_score": float, "relevance_score": float, "cleanliness_score": float, "reason": str, "suggested_search_query": str}}`.
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

        if not scenes_media:
            return {}
        keys = settings.get_gemini_keys_pool()
        if not keys:
            return {}

        anchor_topic = str(primary_entity or "").strip()
        if not anchor_topic:
            first_narrs = [str(it.get("narration") or "").strip() for it in scenes_media[:2] if isinstance(it, dict)]
            anchor_topic = " | ".join(x for x in first_narrs if x) or "General Documentary"

        is_widescreen_sheet = any(
            "16X9" in str(it.get("production_method") or "").upper()
            or str(it.get("media_path") or "").lower().endswith("_16x9.mp4")
            for it in scenes_media
        ) or len(scenes_media) >= 14
        tw, th = (320, 180) if is_widescreen_sheet else (220, 390)
        from concurrent.futures import ThreadPoolExecutor

        thumbs: List[tuple[int, Image.Image, str]] = []
        with tempfile.TemporaryDirectory() as td:
            def _extract_cs_thumb(item: Dict[str, Any]) -> Optional[tuple[int, Image.Image, str]]:
                sid = int(item.get("scene_id") or 0)
                mpath = str(item.get("media_path") or "")
                prod_m_cs = str(item.get("production_method") or "")
                narr = str(item.get("narration") or item.get("visual_keyword") or "")
                if sid <= 0 or not mpath or not Path(mpath).exists():
                    return None
                if (
                    "DOODLE_2D" in prod_m_cs
                    or mpath.lower().endswith(("_doodle_2d.mp4", "_doodle.mp4"))
                ):
                    return None
                p = Path(mpath)
                im_obj: Optional[Image.Image] = None
                try:
                    if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
                        f_pattern = str(Path(td) / f"s_{sid}_%d.jpg")
                        subprocess.run(
                            [
                                "ffmpeg", "-v", "error", "-y",
                                "-t", "5.5", "-i", str(p),
                                "-vf", f"fps=1,scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}",
                                "-frames:v", "5",
                                f_pattern,
                            ],
                            capture_output=True,
                            timeout=8,
                        )
                        cand_frames = [
                            cf for cf in sorted(Path(td).glob(f"s_{sid}_*.jpg"))
                            if cf.exists() and cf.stat().st_size > 1000 and not cls._is_solid_color_or_black(str(cf))
                        ]
                        best_f_jpg: Optional[Path] = None
                        if cand_frames:
                            scored_cands = [
                                (idx_cf, cf, cls._measure_sharpness(str(cf)))
                                for idx_cf, cf in enumerate(cand_frames)
                            ]
                            max_sh_val = max(sh for _, _, sh in scored_cands)
                            mid_sharp = [
                                (idx_cf, cf, sh)
                                for idx_cf, cf, sh in scored_cands
                                if idx_cf in (1, 2, 3) and sh >= max(10.5, max_sh_val * 0.85)
                            ]
                            if mid_sharp:
                                best_f_jpg = max(mid_sharp, key=lambda x: x[2])[1]
                            else:
                                best_f_jpg = max(scored_cands, key=lambda x: x[2])[1]
                        else:
                            f_jpg = Path(td) / f"s_{sid}.jpg"
                            subprocess.run(
                                [
                                    "ffmpeg", "-v", "error", "-y",
                                    "-ss", "0.8", "-i", str(p),
                                    "-frames:v", "1", "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}",
                                    str(f_jpg),
                                ],
                                capture_output=True,
                                timeout=8,
                            )
                            if f_jpg.exists() and f_jpg.stat().st_size > 1000:
                                best_f_jpg = f_jpg
                        if best_f_jpg is not None:
                            with Image.open(str(best_f_jpg)) as im_tmp:
                                im_obj = im_tmp.convert("RGB").copy()
                    else:
                        with Image.open(str(p)) as im_tmp:
                            im_rgb = im_tmp.convert("RGB")
                            im_obj = im_rgb.resize((tw, th), Image.Resampling.BILINEAR)
                except Exception:
                    im_obj = None

                if im_obj is not None:
                    hdr_h = 26
                    card = Image.new("RGB", (tw, th + hdr_h), (16, 16, 20))
                    card.paste(im_obj, (0, hdr_h))
                    draw = ImageDraw.Draw(card)
                    draw.rectangle([(0, 0), (tw, hdr_h)], fill=(0, 0, 0))
                    draw.text((8, 5), f"SCENE #{sid}", fill=(255, 230, 0))
                    vis_kw_cs = str(item.get("visual_keyword") or "").strip()
                    if vis_kw_cs and vis_kw_cs.lower() not in narr.lower():
                        label_desc = f"{narr[:180]} [Directed B-Roll Query: {vis_kw_cs[:70]}]"
                    else:
                        label_desc = narr[:220]
                    return (sid, card, label_desc)
                return None

            if not hasattr(cls, "_cs_scene_pass_cache"):
                cls._cs_scene_pass_cache = {}
            if not hasattr(cls, "_cs_scene_fail_cache"):
                cls._cs_scene_fail_cache = {}
            scene_file_cache_keys: Dict[int, Tuple[str, int, int, str]] = {}
            all_scene_evals: Dict[int, Dict[str, Any]] = {}
            scenes_to_extract: List[Dict[str, Any]] = []
            cached_ref_candidates: List[Dict[str, Any]] = []
            for it_m in scenes_media:
                if isinstance(it_m, dict) and it_m.get("scene_id") and it_m.get("media_path"):
                    try:
                        sid_m = int(it_m["scene_id"])
                        p_m = Path(str(it_m["media_path"]))
                        if p_m.exists():
                            st_m = p_m.stat()
                            ck_m = (str(p_m.resolve()), st_m.st_mtime_ns, st_m.st_size, anchor_topic)
                            scene_file_cache_keys[sid_m] = ck_m
                            if ck_m in cls._cs_scene_pass_cache:
                                all_scene_evals[sid_m] = dict(cls._cs_scene_pass_cache[ck_m])
                                cached_ref_candidates.append(it_m)
                                continue
                            if ck_m in cls._cs_scene_fail_cache:
                                all_scene_evals[sid_m] = dict(cls._cs_scene_fail_cache[ck_m])
                                continue
                    except Exception:
                        pass
                scenes_to_extract.append(it_m)

            if not scenes_to_extract:
                return all_scene_evals

            if cached_ref_candidates and len(scenes_to_extract) < len(scenes_media):
                scenes_for_sheet = cached_ref_candidates[:2] + scenes_to_extract
            else:
                scenes_for_sheet = scenes_to_extract if scenes_to_extract else scenes_media

            with ThreadPoolExecutor(max_workers=min(max(len(scenes_for_sheet), 1), 8)) as cs_thumb_pool:
                for t_res in cs_thumb_pool.map(_extract_cs_thumb, scenes_for_sheet):
                    if t_res is not None:
                        thumbs.append(t_res)

        if not thumbs:
            return all_scene_evals

        rotator = KeyRotator(settings.get_gemini_keys_pool())
        vision_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
        batch_chunk_size = 20
        for b_start in range(0, len(thumbs), batch_chunk_size):
            chunk_thumbs = thumbs[b_start : b_start + batch_chunk_size]
            cols = min(4, len(chunk_thumbs))
            rows = (len(chunk_thumbs) + cols - 1) // cols
            cell_h = th + 26
            sheet = Image.new("RGB", (cols * tw, rows * cell_h), (12, 12, 12))
            scene_lines: List[str] = []
            for idx, (sid, im_t, narr_t) in enumerate(chunk_thumbs):
                r_i = idx // cols
                c_i = idx % cols
                sheet.paste(im_t, (c_i * tw, r_i * cell_h))
                scene_lines.append(f"- SCENE #{sid}: \"{narr_t}\"")

            buf = io.BytesIO()
            sheet.save(buf, format="JPEG", quality=80)
            b64_sheet = base64.b64encode(buf.getvalue()).decode("ascii")

            niche_yaml_visual_block = ""
            try:
                from app.services.niche_manager import build_llm_production_overview_prompt
                eff_niche_cs = str(
                    channel_style
                    or (scenes_media[0].get("channel_style") if scenes_media and isinstance(scenes_media[0], dict) else "")
                    or ""
                ).strip()
                if not eff_niche_cs:
                    top_l = anchor_topic.lower()
                    if any(k in top_l for k in ("psychology", "brain", "cortex", "interrogation", "fbi", "suspect", "phineas gage", "experiment", "casino", "dopamine")):
                        eff_niche_cs = "psychology"
                if eff_niche_cs:
                    niche_yaml_visual_block = "\n" + build_llm_production_overview_prompt(eff_niche_cs, include_all_niches_map=False)[:1800] + "\n"
            except Exception:
                pass

            prompt = f"""You are the Chief Multimodal Visual Critic inspecting and self-scoring the final scene frames of a YouTube {'16:9 Documentary' if is_widescreen_sheet else 'Short'} BEFORE rendering.
Overall Script Topic & Named Protagonists: "{anchor_topic}"{niche_yaml_visual_block}
Scenes in the attached contact sheet (labeled SCENE #1, SCENE #2, ... along with each scene's spoken narration):
{chr(10).join(scene_lines)}

SELF-SCORING PROTOCOL FOR EVERY SCENE TILE (0.0 to 10.0):
1. CROSS-SCENE COREFERENCE & CROSS-TILE VISUAL IDENTITY CONSISTENCY (`entity_match_score`: 0.0–10.0):
   - Resolve any pronouns or generic nouns in each scene's narration (e.g., "the hunter", "the eagle", "its prey", "this creature", "he/she/it") back to the exact named protagonists in Overall Script Topic ("{anchor_topic}").
   - 2-TIER DOCUMENTARY B-ROLL RULE: In any two-entity matchup, comparison, or geographical/natural mystery ("{anchor_topic}"), BOTH named protagonists AND the named geographical setting/environment (as well as visually similar species/vessels/aircraft/landscapes from the same class on free stock/documentary libraries, such as monitor lizards for Komodo dragon, crocodile/alligator for caiman, any wild viper/snake for an endemic island snake, brown bear for grizzly, uninhabited island cliffs/ocean waves/rainforest canopy for an island mystery, white glaciers/ice walls/subglacial water for an Antarctic mystery, desert sand dunes/shipwrecks for a coastal desert mystery, or authentic WWII/naval battleships, naval artillery turrets firing, ocean combat explosions, warship silhouettes on the horizon with smoke/shell splashes, ship rudder/propeller underwater or drydock shots, stormy Atlantic ocean waves illustrating a trapped/drifting vessel at sea, and vintage military biplanes/warplanes in flight for a naval/aerial war topic) are 100% VALID primary subjects! A clean shot showing AT LEAST ONE of the named protagonists, class-equivalent species/vessels, OR the canonical geographical setting (island shoreline, ocean cliffs, glacier, desert coast, forest canopy) from "{anchor_topic}" in ANY natural wild season, weather, or time-of-day lighting (e.g., green summer/autumn forest, meadow, or tundra vs winter snow; sunny vs overcast) MUST score `entity_match_score >= 8.5` and `relevance_score >= 8.5` (`"passed": true`). NEVER fail a tile because only one of the two combatants is visible in that frame, because natural wild B-roll of the exact species was filmed in a different season/weather (such as grass/forest instead of snow) than another scene, because a scene shows the geographical island/ocean/glacier setting while the voiceover describes venom/temperature/mechanisms, because an authentic archival war film or horizon naval shot has vintage film grain/smoke/ocean waves without a readable ship nameplate, or because an authentic preserved battleship has a naval hull pennant number! Only score `entity_match_score < 5.0` if the tile shows a completely unrelated animal taxonomic group or modern civilian airshows with tourists/bright red civilian sports monoplanes/Commemorative Air Force tarmac.
   - PSYCHOLOGY / NEUROSCIENCE / MEDICAL / PRE-CINEMA HISTORICAL CASE STUDY B-ROLL RULE: Whenever the Overall Script Topic ("{anchor_topic}") involves psychology, neuroscience, brain anatomy/mechanisms (e.g., prefrontal cortex, synapses, neural pathways, split-brain), a medical case study, or a 19th-century/pre-cinema historical figure where live footage of the accident/event cannot exist (e.g., Phineas Gage 1848), ANY scene tile in the video — EVEN IF that specific scene's narration or query describes a physical injury, explosion, skull trauma, surgery, or 19th-century survivor action — that shows 3D glowing brain/neural/synapse activity, microscopic neurons, medical skull/CT reconstructions, archival historical portraits/engravings, OR cinematic/monochrome human behavioral/walking/emotional B-roll (even with contemporary clothing/streets on free stock libraries where 19th-century period video does not exist) IS 100% VALID primary documentary B-roll and MUST score `entity_match_score >= 8.5`, `relevance_score >= 8.5`, and `cleanliness_score >= 8.5` (`"passed": true`)! NEVER fail any scene in a psychology/neuroscience/pre-cinema case study topic for showing 3D neural/brain activity or contemporary stock human behavioral/emotional B-roll instead of 19th-century period actors!
2. NARRATIVE ACTION & SETTING ALIGNMENT (`relevance_score`: 0.0–10.0):
   - Does the frame's subject, setting, and historical era align with a documentary on "{anchor_topic}" or the scene's `[Directed B-Roll Query: ...]`?
   - ZERO LITERAL METAPHOR OR ABSTRACT WORD-TRAP OBJECTS: If the spoken narration uses a metaphor or abstract noun (e.g., "a floating hunk of steel", "iron trap", "record profits", "paper trail"), NEVER pass (`relevance_score < 3.0`, `"passed": false`) an indoor metal pipe, ventilation grate, factory wall, tool, household/office recycling or trash bin (e.g. labeled `"PAPER"`), cardboard box, or generic office clutter! The frame MUST show the actual canonical entity/setting from "{anchor_topic}".
   - Apply the 2-TIER DOCUMENTARY B-ROLL RULE: when a spoken narration chunk describes a complex attack, internal physiological/mechanical/neurological mechanism, or fatal outcome ("already dead", "collapses", "waits", "rewired his personality"), any clean authentic documentary shot (including archival historical combat film, 3D anatomical/brain/neuroscience animations, medical skull/CT illustrations, or 3D historical/naval documentary reconstructions) showing AT LEAST ONE of the canonical named protagonists or anatomical/psychological subjects from "{anchor_topic}" MUST score `relevance_score >= 8.5` (`"passed": true`). NEVER fail a scene just because a stock B-roll frame shows the animal/vessel intact or shows 3D neural/brain pathways or human emotional expressions while the voiceover describes an injury, internal mechanism, or historical case!
3. VISUAL CLEANLINESS & HISTORICAL/CONTEXTUAL AUTHENTICITY (`cleanliness_score`: 0.0–10.0):
   - Score `< 3.5` (`"passed": false`) if the frame contains magazine/infographic text, callout arrows, channel logos, uploader watermarks ("By: ..."), black letterbox/pillarbox bars, AI corner badges/sparkle icons (`✦` Gemini/Veo in any of the 4 corners), surreal AI deformities, captive zoo/farm wire-mesh fences/cages in a wild nature or historical story, modern civilian cars/paved highways/street signs or modern airshow spectators in T-shirts during a pre-1950 historical story, or foreign national flags/military insignia/painted hull or fuselage text (e.g., Cyrillic `"НЕ БРАТЬСЯ"`) that contradict the historical country/subject in "{anchor_topic}".
4. SELF-CORRECTION QUERIES (`suggested_search_query` & `fallback_broll_query`):
   - A scene passes (`"passed": true`) ONLY if `entity_match_score >= 7.5`, `relevance_score >= 7.5`, AND `cleanliness_score >= 7.5`.
   - Whenever `"passed": false`, provide TWO concise 2–4 word English queries:
     + `suggested_search_query`: Names the exact primary entity + action/feature from "{anchor_topic}" (for specialist documentary video search).
     + `fallback_broll_query`: Names either the exact primary entity OR the exact co-protagonist from "{anchor_topic}" in a broader natural documentary pose (e.g., perched on branch, hanging in canopy, swimming in ocean). NEVER output subjectless landscape queries unless the spoken narration chunk itself describes pure scenery without any entity.

Return ONLY valid JSON in this exact format:
{{
  "scene_evaluations": [
    {{
      "scene_id": 1,
      "entity_match_score": 9.2,
      "relevance_score": 9.0,
      "cleanliness_score": 9.5,
      "passed": true,
      "reason": "Exact named subject and habitat match",
      "suggested_search_query": "",
      "fallback_broll_query": ""
    }}
  ]
}}"""

            for model_name, api_key in rotator.iter_best_model_key_pairs(vision_models, max_total_attempts=4):
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
                    t_req_cs = time.monotonic()
                    resp = requests.post(url, json=payload, timeout=KeyRotator.fast_timeout(18))
                    cs_elapsed = time.monotonic() - t_req_cs
                    if resp.status_code == 200:
                        rotator.record_model_success(api_key, model_name, cs_elapsed)
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                data = json.loads(m.group(0))
                                for item_ev in (data.get("scene_evaluations") or []):
                                    if isinstance(item_ev, dict) and item_ev.get("scene_id"):
                                        sid_e = int(item_ev["scene_id"])
                                        ent_s = float(item_ev.get("entity_match_score", 9.0))
                                        rel_s = float(item_ev.get("relevance_score", 9.0))
                                        cln_s = float(item_ev.get("cleanliness_score", 9.0))
                                        sug_q_raw = str(item_ev.get("suggested_search_query") or "").strip()
                                        broll_q_raw = str(item_ev.get("fallback_broll_query") or "").strip()
                                        raw_passed = bool(item_ev.get("passed", True))
                                        min_sub = min(ent_s, rel_s, cln_s)
                                        avg_sub = (ent_s + rel_s + cln_s) / 3.0
                                        is_p = (
                                            (raw_passed and min_sub >= 7.5)
                                            or (raw_passed and min_sub >= 6.8 and avg_sub >= 7.5)
                                            or (not sug_q_raw and not broll_q_raw and min_sub >= 6.5 and avg_sub >= 7.2)
                                        )
                                        if is_p and min_sub < 7.5:
                                            ent_s = max(ent_s, 8.0)
                                            rel_s = max(rel_s, 8.0)
                                            cln_s = max(cln_s, 8.0)
                                        ck_e = scene_file_cache_keys.get(sid_e)
                                        if not is_p and ck_e and ck_e in cls._cs_scene_pass_cache:
                                            all_scene_evals[sid_e] = dict(cls._cs_scene_pass_cache[ck_e])
                                            continue
                                        if is_p and ck_e and ck_e in cls._cs_scene_fail_cache:
                                            cls._cs_scene_fail_cache.pop(ck_e, None)
                                        ev_entry = {
                                            "passed": is_p,
                                            "entity_match_score": ent_s,
                                            "relevance_score": rel_s,
                                            "cleanliness_score": cln_s,
                                            "reason": str(item_ev.get("reason") or ""),
                                            "suggested_search_query": "" if is_p else sug_q_raw,
                                            "fallback_broll_query": "" if is_p else broll_q_raw,
                                        }
                                        all_scene_evals[sid_e] = ev_entry
                                        if is_p and ck_e and min(ent_s, rel_s, cln_s) >= 7.5:
                                            cls._cs_scene_pass_cache[ck_e] = dict(ev_entry)
                                            cls._cs_scene_fail_cache.pop(ck_e, None)
                                        elif not is_p and ck_e and (sug_q_raw or broll_q_raw) and min_sub < 6.5:
                                            cls._cs_scene_fail_cache[ck_e] = dict(ev_entry)
                                for item_f in (data.get("failed_scenes") or []):
                                    if isinstance(item_f, dict) and item_f.get("scene_id"):
                                        sid_f = int(item_f["scene_id"])
                                        fail_entry = {
                                            "passed": False,
                                            "entity_match_score": 4.0,
                                            "relevance_score": 4.0,
                                            "cleanliness_score": 4.0,
                                            "reason": str(item_f.get("reason") or "Lệch chủ đề/chứa chữ rác"),
                                            "suggested_search_query": str(item_f.get("suggested_search_query") or "").strip(),
                                        }
                                        all_scene_evals[sid_f] = fail_entry
                                        ck_f = scene_file_cache_keys.get(sid_f)
                                        if ck_f and ck_f not in cls._cs_scene_pass_cache:
                                            cls._cs_scene_fail_cache[ck_f] = dict(fail_entry)
                                break
                    elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except (json.JSONDecodeError, ValueError) as e_vp:
                    logger.debug(f"[VisualCritic] ContactSheet JSON parse skip ({model_name}): {e_vp}")
                    continue
                except Exception as e_v:
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(e_v)[:200])
                    continue
        return all_scene_evals

    @trace_custom_span("VisualCritic.evaluate_media", run_type="chain")
    def evaluate_media(
        self,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        channel_style: Optional[str] = None,
    ) -> VisualEvaluationReport:
        """
        Kiểm định SONG SONG ĐỘ SẮC NÉT, ĐỘ PHÂN GIẢI, TÍNH TOÀN VẸN BITMAP & CHỐNG TRÙNG LẶP của tất cả các cảnh.
        Sử dụng ThreadPoolExecutor để kiểm tra đồng loạt toàn bộ các cảnh cùng lúc.
        """
        from concurrent.futures import ThreadPoolExecutor

        def _inspect_single_scene(item: Dict[str, Any]) -> Dict[str, Any]:
            scene_id = item.get("scene_id", 0)
            media_path = item.get("media_path", "")
            media_type = item.get("media_type") or (
                "video" if str(media_path).lower().endswith((".mp4", ".mov", ".webm", ".mkv")) else "image"
            )
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

            if p.name.lower().endswith("_graphic.png") or str(item.get("production_method") or "").upper() == "EMERGENCY_GRAPHIC_CARD":
                return {
                    "scene_id": scene_id,
                    "media_path": media_path,
                    "rel_score": 1.0,
                    "entity_score": 1.0,
                    "aes_score": 1.0,
                    "valid_bitmap": False,
                    "issues": ["Còn sót thẻ chữ placeholder (_graphic.png) chưa được thay bằng tư liệu thật"],
                    "failed": True,
                    "img_hash": "",
                    "sig": [],
                }

            failed = False
            prod_m_raw = str(item.get("production_method") or "")
            is_doodle_clip = False
            is_longform_chapter_clip = (
                prod_m_raw.startswith("LONGFORM_")
                or p.name.lower().endswith("_16x9.mp4")
            )
            # MỌI cảnh video thật (kể cả Cảnh 1 & Cảnh 2) BẮT BUỘC phải kiểm tra trùng dHash để cấm tuyệt đối 2 cảnh cắt từ cùng 1 nguồn!
            is_long_peak_cont = False
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
                        pose_list = [
                            str(pp)
                            for pp in (item.get("pose_images") or [str(p)])
                            if pp and Path(str(pp)).exists() and not str(pp).endswith("_graphic.png")
                        ]
                        if not pose_list:
                            pose_list = [str(p)]
                        sig = []
                        for pp in pose_list:
                            h_val = self._compute_image_hash(pp)
                            if h_val:
                                if any(sum(a != b for a, b in zip(h_val, prev_h)) <= 8 for prev_h in sig):
                                    issues.append(f"Hai ảnh trong cùng Cảnh #{scene_id} bị trùng lặp góc máy")
                                    aes_score -= 3.5
                                    failed = True
                                else:
                                    sig.append(h_val)
                        img_hash = sig[0] if sig else None
                except Exception as ex:
                    valid_bitmap = False
                    issues.append(f"Lỗi đọc file ảnh với Pillow: {ex}")
                    failed = True
            else:
                try:
                    from app.utils.ffmpeg_helper import get_duration_ms as _vc_get_dur
                    if _vc_get_dur(str(p)) < 800:
                        valid_bitmap = False
                        aes_score = 1.0
                        issues.append("Video clip bị lỗi hỏng / thiếu moov atom (duration < 0.8s)")
                        failed = True
                        sig = []
                    else:
                        sig = [] if is_long_peak_cont else self._media_signature(str(p))
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
                            req_s1_sh = 8.0 if (is_longform_chapter_clip or p.name.lower().endswith("_16x9.mp4")) else 12.0
                            is_hook_preverified = bool(item.get("hook_verified"))
                            pre_hook_sh = float(item.get("hook_sharpness") or 0.0)
                            if is_hook_preverified and pre_hook_sh >= req_s1_sh:
                                ok_s1_sh, s1_sh_val = True, pre_hook_sh
                            else:
                                ok_s1_sh, s1_sh_val = _ensure_hook_clip_sharpness_and_luma(str(p), min_sharpness=req_s1_sh)
                            if not ok_s1_sh:
                                aes_score = max(3.8, round(s1_sh_val * 0.5, 1))
                                issues.append(
                                    f"Khung hình mở đầu (Cảnh 1) chưa đạt chuẩn độ sắc nét (sharpness={s1_sh_val:.2f} < {req_s1_sh})"
                                )
                                failed = True
                            else:
                                clip_gate = self.inspect_clip_before_concat(
                                    str(p),
                                    min_short_edge=720,
                                    min_sharpness=5.8,
                                    check_watermark=not is_hook_preverified,
                                )
                                aes_score = float(clip_gate.get("score", 9.2 if is_pregraded_hook else 8.0))
                                if not clip_gate.get("passed", True):
                                    issues.extend(clip_gate.get("issues") or ["Clip Cảnh 1 không đạt cổng kiểm duyệt chất lượng/watermark"])
                                    failed = True
                        else:
                            clip_gate = self.inspect_clip_before_concat(
                                str(p),
                                min_short_edge=720,
                                min_sharpness=5.8,
                                check_watermark=True,
                            )
                            aes_score = float(clip_gate.get("score", 9.2 if is_pregraded_hook else 8.0))
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

        max_workers = min(max(len(scenes_media), 1), 12)
        vision_scene_evals: Dict[int, Dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=max_workers + 1)
        try:
            cs_future = pool.submit(
                self._gemini_vision_audit_contact_sheet,
                scenes_media,
                primary_entity,
                channel_style,
            )
            inspected_list = list(pool.map(_inspect_single_scene, scenes_media))
            try:
                vision_scene_evals = cs_future.result(timeout=35.0) or {}
                failed_cs_map = {
                    k: v.get("reason")
                    for k, v in vision_scene_evals.items()
                    if isinstance(v, dict) and not v.get("passed", True)
                }
                if failed_cs_map:
                    logger.warning(
                        f"👁️ [VisualCritic ContactSheet Vision] Phát hiện {len(failed_cs_map)} cảnh chưa đạt chuẩn ngữ nghĩa/thực thể: "
                        f"{failed_cs_map}"
                    )
            except Exception as e_cs:
                logger.warning(f"⚠️ [VisualCritic ContactSheet Vision] Quá hạn hoặc gián đoạn API ({e_cs}) — sử dụng bộ kiểm định CV kỹ thuật.")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        scene_media_by_id: Dict[int, Dict[str, Any]] = {
            int(m.get("scene_id") or 0): m for m in scenes_media if isinstance(m, dict) and m.get("scene_id")
        }
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

            v_eval = vision_scene_evals.get(scene_id)
            if isinstance(v_eval, dict):
                res["rel_score"] = float(v_eval.get("relevance_score", res["rel_score"]))
                res["entity_score"] = float(v_eval.get("entity_match_score", res["entity_score"]))
                cln_s = float(v_eval.get("cleanliness_score", 9.0))
                if not v_eval.get("passed", True) or min(res["rel_score"], res["entity_score"], cln_s) < 7.5:
                    v_reason = str(v_eval.get("reason") or "Lệch thực thể/ngữ cảnh kịch bản")
                    v_sug_q = str(v_eval.get("suggested_search_query") or "").strip()
                    v_broll_q = str(v_eval.get("fallback_broll_query") or "").strip()
                    issues.append(
                        f"Gemini Vision ContactSheet: {v_reason}"
                        + (f" [Suggested Query: {v_sug_q}]" if v_sug_q else "")
                        + (f" [Fallback B-Roll: {v_broll_q}]" if v_broll_q else "")
                    )
                    aes_score = min(aes_score, cln_s, 5.5)
                    failed = True
                    target_m = scene_media_by_id.get(int(scene_id))
                    if isinstance(target_m, dict):
                        target_m["critic_feedback"] = v_reason
                        if v_sug_q:
                            target_m["critic_suggested_query"] = v_sug_q
                        if v_broll_q:
                            target_m["critic_fallback_broll_query"] = v_broll_q

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
        overall = (
            sum((s.relevance_score + s.entity_match_score + s.aesthetic_score) / 3.0 for s in scene_scores) / total_scenes
            if total_scenes > 0
            else 0.0
        )
        passed = len(failed_scene_ids) == 0 and overall >= 7.5

        return VisualEvaluationReport(
            scene_scores=scene_scores,
            overall_score=round(overall, 1),
            failed_scene_ids=list(set(failed_scene_ids)),
            passed=passed,
            summary="\n".join(issues_summary) if issues_summary else "Tất cả hình ảnh/video đạt chuẩn ngữ nghĩa, thực thể & độ sắc nét."
        )
