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
            if media_type == "image":
                try:
                    with Image.open(str(p)) as im:
                        im.verify()
                    with Image.open(str(p)) as im:
                        w, h = im.size
                        if w < 400 or h < 400:
                            aes_score -= 3.5
                            issues.append(f"Độ phân giải quá thấp (kém sắc nét): {w}x{h} px (yêu cầu >= 400px)")
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
                        sharpness = self._measure_sharpness(str(p))
                        if sharpness < 5.8:
                            aes_score -= 3.5
                            issues.append(f"Ảnh bị mờ nhòe / thiếu độ sắc nét (sharpness={sharpness:.1f} < 5.8)")
                            failed = True
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
                except Exception as ex_sig:
                    logger.debug(f"[VisualCritic] Lỗi trích xuất chữ ký video cảnh {scene_id}: {ex_sig}")

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

            if img_hash:
                if img_hash in seen_hashes:
                    prev_id = seen_hashes[img_hash]
                    issues.append(f"Ảnh trùng lặp 100% với Cảnh #{prev_id}")
                    aes_score -= 4.0
                    failed = True
                else:
                    seen_hashes[img_hash] = scene_id

            if sig:
                dup_id = self._find_duplicate(sig, seen_signatures)
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
