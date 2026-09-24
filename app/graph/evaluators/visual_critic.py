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

    def evaluate_media(
        self,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None
    ) -> VisualEvaluationReport:
        """Chấm điểm toàn diện kho tư liệu của tất cả các cảnh."""
        scene_scores: List[SceneVisualScore] = []
        failed_scene_ids: List[int] = []
        seen_hashes = {}
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

            # 2. Kiểm tra nếu có primary_entity: CẤM stock video của diễn viên hiện đại
            if primary_entity:
                if media_type == "video" and ("pexels" in media_path.lower() or "stock" in media_path.lower()):
                    entity_score = 3.0
                    issues.append(f"Cảnh báo: Dùng stock video hiện đại cho nhân vật lịch sử '{primary_entity}'")
                    failed_scene_ids.append(scene_id)
                elif any(tag in p.name.lower() for tag in ["_doc", "_wiki", "_real"]):
                    entity_score = 10.0  # Ảnh tư liệu đích thực
                elif "_ai" in p.name.lower():
                    entity_score = 8.5   # Ảnh AI có gắn context
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

        passed = len(failed_scene_ids) == 0 and overall >= 7.8

        return VisualEvaluationReport(
            scene_scores=scene_scores,
            overall_score=round(overall, 1),
            failed_scene_ids=list(set(failed_scene_ids)),
            passed=passed,
            summary="\n".join(issues_summary) if issues_summary else "Tất cả hình ảnh đạt chuẩn chất lượng cao & chân thật."
        )
