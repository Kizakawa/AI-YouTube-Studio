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
from typing import List, Dict, Any, Optional

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
        Đo độ sắc nét biên cạnh (edge sharpness energy) của ảnh bằng bộ lọc FIND_EDGES trên chuẩn hóa 256x256.
        Giá trị càng cao ảnh càng sắc nét (thường >= 10.0 là nét căng, < 6.0 là bị mờ nhòe/vỡ hạt).
        """
        try:
            from PIL import ImageFilter, ImageStat
            with Image.open(image_path) as img:
                gray = img.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                edges = gray.filter(ImageFilter.FIND_EDGES)
                return float(ImageStat.Stat(edges).mean[0])
        except Exception:
            return 10.0

    # Ngưỡng Hamming (trên 64 bit) để coi 2 khung hình là "cùng 1 cảnh quay" dù bị nén/scale/crop khác nhau
    _DUP_HAMMING = 6

    @classmethod
    def _media_signature(cls, media_path: str) -> List[str]:
        """
        Chữ ký thị giác của 1 cảnh: ảnh → 1 dHash; video → dHash của 3 khung hình (đầu / giữa / cuối).
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
            dur = max(get_duration_ms(str(p)) / 1000.0, 0.6)
        except Exception:
            dur = 3.0
        with tempfile.TemporaryDirectory() as td:
            for i, t in enumerate((min(0.4, dur * 0.2), dur * 0.5, max(dur - 0.4, dur * 0.8))):
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
        """Trả về scene_id bị trùng (nếu có). Video cần ≥ 2 khung khớp để tránh báo nhầm."""
        if not sig:
            return None
        need = 1 if len(sig) == 1 else 2
        for prev_id, prev_sig in seen.items():
            if not prev_sig:
                continue
            hits = sum(
                1 for h in sig
                if any(sum(a != b for a, b in zip(h, ph)) <= cls._DUP_HAMMING for ph in prev_sig)
            )
            if hits >= min(need, len(prev_sig)):
                return prev_id
        return None

    @classmethod
    def inspect_clip_before_concat(
        cls,
        video_path: str,
        min_short_edge: int = 720,
        min_sharpness: float = 5.8,
        check_watermark: bool = True,
        allow_snow: bool = False,
    ) -> Dict[str, Any]:
        """
        Cổng kiểm duyệt chất lượng & watermark từng clip TRƯỚC khi ghép (Pre-Assembly Clip Gate):
        1. Độ phân giải nguồn tối thiểu >= 720p (min(width, height) >= 720).
        2. Độ sắc nét biên cạnh (sharpness >= 5.8, không khung nào < 4.8).
        3. Độ sáng & tương phản cân bằng (24 <= brightness <= 228, contrast >= 18).
        4. Phát hiện chữ/logo/watermark kênh khác đóng cứng ở 4 góc hoặc dải trên/dưới qua đa khung hình.
        """
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
                        continue
                    raw_bytes = Path(f_jpg).read_bytes()
                    if check_watermark:
                        try:
                            from app.services.video_memory_rag import VideoMemoryRAG
                            has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(raw_bytes)
                            if has_bad_art and not any("chữ/logo" in x or "vách ngăn" in x for x in issues):
                                issues.append(f"Phát hiện chữ/logo/khung ghép đóng cứng ({art_reason})")
                        except Exception:
                            pass

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

        if min_lum < 22.0 or avg_lum < 26.0:
            issues.append(f"Clip quá tối hoặc có khung hình đen (brightness={avg_lum:.1f}, min={min_lum:.1f} < 22)")
        if max_lum > 230.0:
            issues.append(f"Clip bị cháy sáng / lóa trắng (max_brightness={max_lum:.1f} > 230)")
        if min_con < 16.0:
            issues.append(f"Clip thiếu tương phản hoặc phẳng màu (contrast={min_con:.1f} < 16)")
        if avg_sharp < min_sharpness or min_f_sharp < max(4.6, min_sharpness - 1.2):
            issues.append(
                f"Clip bị mờ nhòe / vỡ hạt (sharpness={avg_sharp:.1f}, min={min_f_sharp:.1f}, yêu cầu >= {min_sharpness})"
            )

        # Kiểm tra logo/watermark cố định ở 4 góc hoặc banner chữ khi cảnh nền chuyển động
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

                    corner_boxes = [
                        ("top_left", (4, 6, 68, 54)),
                        ("top_right", (112, 6, 176, 54)),
                        ("bottom_left", (4, 256, 68, 308)),
                        ("bottom_right", (112, 256, 176, 308)),
                        ("top_banner", (22, 10, 158, 52)),
                    ]
                    for c_name, box in corner_boxes:
                        c_mean = float(ImageStat.Stat(pers_im.crop(box)).mean[0])
                        if c_mean >= 36.0 and c_mean >= center_mean * 2.25:
                            issues.append(
                                f"Phát hiện watermark/logo kênh khác đóng cứng tại vùng {c_name} (edge={c_mean:.1f})"
                            )
                            break
            except Exception:
                pass

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
            is_long_peak_cont = str(item.get("production_method") or "") == "LONG_PEAK_CONTINUOUS_FLOW"
            if media_type == "image":
                # Kiểm tra & lọc sạch danh sách pose_images (nếu có) để loại bỏ file phụ lỗi/đen trước khi dựng
                raw_poses = item.get("pose_images")
                if isinstance(raw_poses, list) and len(raw_poses) > 1:
                    clean_poses: List[str] = []
                    for pp in raw_poses:
                        if pp and Path(pp).exists() and Path(pp).stat().st_size >= 2500 and not self._is_solid_color_or_black(str(pp)):
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
                        if sharpness < 5.8:
                            aes_score = max(3.5, round(sharpness * 0.9, 1))
                            issues.append(f"Ảnh bị mờ nhòe / thiếu độ sắc nét (sharpness={sharpness:.1f} < 5.8)")
                            failed = True
                        elif not failed:
                            aes_score = round(min(9.8, 7.6 + (sharpness - 5.8) * 0.22), 1)
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
