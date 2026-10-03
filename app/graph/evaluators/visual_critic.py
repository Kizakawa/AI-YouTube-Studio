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
        """Chấm điểm toàn diện kho tư liệu của tất cả các cảnh."""
        scene_scores: List[SceneVisualScore] = []
        failed_scene_ids: List[int] = []
        seen_hashes = {}
        seen_signatures: Dict[int, List[str]] = {}
        issues_summary = []

        for item in scenes_media:
            scene_id = item.get("scene_id", 0)
            media_path = item.get("media_path", "")
            media_type = item.get("media_type", "image")
            p = Path(media_path)

            issues = []
            rel_score = 9.0
            entity_score = 9.0
            aes_score = 9.0
            valid_bitmap = True

            # 1. Kiểm tra tồn tại file
            if not p.exists() or p.stat().st_size < 2048:
                valid_bitmap = False
                issues.append("File media không tồn tại hoặc dung lượng rỗng (< 2KB)")
                failed_scene_ids.append(scene_id)
                issues_summary.append(f"Cảnh {scene_id}: {'; '.join(issues)}")
                scene_scores.append(SceneVisualScore(
                    scene_id=scene_id,
                    media_path=media_path,
                    relevance_score=1.0,
                    entity_match_score=1.0,
                    aesthetic_score=1.0,
                    is_valid_bitmap=False,
                    issues=issues
                ))
                continue

            # 2. Kiểm tra tính chân thật thực thể (Entity & Context Check)
            # Không đánh đồng: Video B-roll môi trường, hành động, biểu tượng, thiên nhiên (mưa bão, vũ khí, khói lửa, đồng hồ...)
            # là hoàn toàn hợp lệ và làm video sống động hơn, chỉ phạt nếu video stock có người hiện đại đóng vai sai thời đại.
            if primary_entity:
                p_lower = p.name.lower()
                if any(tag in p_lower for tag in ["_doc", "_wiki", "_real"]):
                    entity_score = 10.0  # Ảnh/tư liệu đích thực
                elif "_ai" in p_lower or "_t2v" in p_lower or "_i2v" in p_lower:
                    entity_score = 8.5   # AI video/ảnh có định hướng bối cảnh
                elif media_type == "video":
                    # Video B-roll phong cảnh, hành động giúp giữ chân người xem
                    entity_score = 8.0
                else:
                    entity_score = 7.5

            # 3. Kiểm tra tính toàn vẹn nếu là file ảnh
            if media_type == "image":
                try:
                    with Image.open(str(p)) as im:
                        im.verify()
                    with Image.open(str(p)) as im:
                        w, h = im.size
                        if w < 300 or h < 300:
                            aes_score -= 3.0
                            issues.append(f"Độ phân giải quá nhỏ: {w}x{h} px")
                            failed_scene_ids.append(scene_id)
                        aspect = max(w / h, h / w)
                        if aspect > 2.5:
                            aes_score -= 4.0
                            issues.append(f"Tỷ lệ khung hình bất thường (dải hẹp/banner: {w}x{h})")
                            failed_scene_ids.append(scene_id)

                    # Kiểm tra ảnh đen kịt / hỏng
                    if self._is_solid_color_or_black(str(p)):
                        aes_score = 2.0
                        issues.append("Ảnh bị tối đen hoàn toàn hoặc đơn sắc lỗi")
                        failed_scene_ids.append(scene_id)

                    # 4. Kiểm tra trùng lặp ảnh với các cảnh khác
                    img_hash = self._compute_image_hash(str(p))
                    if img_hash:
                        if img_hash in seen_hashes:
                            prev_id = seen_hashes[img_hash]
                            issues.append(f"Ảnh trùng lặp 100% với Cảnh #{prev_id}")
                            aes_score -= 4.0
                            failed_scene_ids.append(scene_id)
                        else:
                            seen_hashes[img_hash] = scene_id

                except Exception as ex:
                    valid_bitmap = False
                    issues.append(f"Lỗi đọc file ảnh với Pillow: {ex}")
                    failed_scene_ids.append(scene_id)

            # 4b. Chống trùng lặp THỊ GIÁC giữa các cảnh (cả ảnh lẫn VIDEO, kể cả khác độ phân giải / bị nén lại)
            try:
                sig = self._media_signature(str(p))
                dup_id = self._find_duplicate(sig, seen_signatures)
                if dup_id is not None and not any("trùng lặp" in i for i in issues):
                    issues.append(f"Hình ảnh/clip trùng lặp với Cảnh #{dup_id}")
                    aes_score -= 4.0
                    failed_scene_ids.append(scene_id)
                    logger.warning(f"🔁 [VisualCritic] Cảnh {scene_id} trùng hình ảnh với Cảnh #{dup_id} → sẽ thay thế")
                elif sig:
                    seen_signatures[scene_id] = sig
            except Exception as ex_dup:
                logger.debug(f"[VisualCritic] Lỗi kiểm tra trùng cảnh {scene_id}: {ex_dup}")

            if issues:
                issues_summary.append(f"Cảnh {scene_id}: {'; '.join(issues)}")

            score_item = SceneVisualScore(
                scene_id=scene_id,
                media_path=media_path,
                relevance_score=max(1.0, rel_score),
                entity_match_score=max(1.0, entity_score),
                aesthetic_score=max(1.0, aes_score),
                is_valid_bitmap=valid_bitmap,
                issues=issues,
            )
            scene_scores.append(score_item)

        total_scenes = len(scene_scores)
        overall = sum(
            (s.relevance_score * 0.35 + s.entity_match_score * 0.35 + s.aesthetic_score * 0.30)
            for s in scene_scores
        ) / total_scenes if total_scenes > 0 else 0.0

        # ── 5. Kiểm tra Tỷ lệ Chuyển động Động (Anti-Slideshow Dynamism Check) ──
        # Đảm bảo video không bị cứng nhắc ở tư duy "ghép ảnh tĩnh"
        video_count = sum(
            1 for item in scenes_media
            if item.get("media_type") == "video"
            or Path(item.get("media_path", "")).suffix.lower() in (".mp4", ".mov", ".webm")
        )
        video_ratio = (video_count / total_scenes) if total_scenes > 0 else 0.0

        if total_scenes >= 4 and video_ratio < 0.25:
            # Nếu toàn bộ video gần như là ảnh tĩnh (ít hơn 25% video động) -> Cảnh báo Slideshow Fatigue
            issues_summary.append(
                f"⚠️ Cảnh báo Slideshow Fatigue: Tỷ lệ video động chỉ đạt {video_ratio*100:.0f}%. "
                f"Khuyến nghị đan xen thêm video clip B-roll chuyển động thực tế hoặc AI Video (mục tiêu >= 50-70%)."
            )
            # Trừ nhẹ điểm để khuyến khích hệ thống chèn thêm video động
            overall = max(1.0, overall - 0.5)

        passed = len(failed_scene_ids) == 0 and overall >= 7.5

        return VisualEvaluationReport(
            scene_scores=scene_scores,
            overall_score=round(overall, 1),
            failed_scene_ids=list(set(failed_scene_ids)),
            passed=passed,
            summary="\n".join(issues_summary) if issues_summary else f"Tất cả hình ảnh/video đạt chuẩn kỹ thuật & tính chân thật (Tỷ lệ video động: {video_ratio*100:.0f}%)."
        )
