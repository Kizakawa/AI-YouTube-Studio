"""
app.graph.evaluators.content_quality_critic — Bộ thẩm định NỘI DUNG hình ảnh bằng AI (Gemini Vision).

Khác biệt cốt lõi so với VisualCritic (kiểm tra kỹ thuật file):
  VisualCritic: Kiểm tra file tồn tại, độ phân giải, trùng lặp hash, ảnh đen (rule-based).
  ContentQualityCritic: NHÌN THẬT vào ảnh bằng Gemini Vision và chấm điểm:
    1. Relevance Score  — Ảnh có liên quan đến lời thoại/visual_keyword không?
    2. Entity Accuracy  — Nhân vật/sự vật trong ảnh có đúng với primary_entity không?
    3. Historical Fidelity — Phong cách, trang phục, bối cảnh có phù hợp thời đại không?
    4. Visual Composition — Bố cục, chất lượng nghệ thuật, tính điện ảnh.
    5. Actionable Feedback — Gợi ý cụ thể cho MediaAgent nếu cần thay thế ảnh.

Workflow tích hợp:
  node_fetch_media → node_evaluate_media (VisualCritic kỹ thuật)
    → node_content_quality_check (ContentQualityCritic AI) → conditional → refine hoặc render
"""
import base64
import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.graph.state import ContentQualityReport, SceneContentScore

logger = logging.getLogger(__name__)

VISION_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
]


class ContentQualityCritic:
    """
    Agent thẩm định nội dung hình ảnh bằng Gemini Vision API.
    Nhìn thật vào từng bức ảnh và đánh giá mức độ phù hợp với kịch bản.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    @staticmethod
    def _encode_image_base64(image_path: str) -> Optional[Dict[str, str]]:
        """Đọc file ảnh, tối ưu kích thước bằng PIL và mã hoá base64 để gửi lên Gemini Vision."""
        try:
            p = Path(image_path)
            if not p.exists() or p.stat().st_size < 1000:
                return None

            try:
                from PIL import Image
                import io
                with Image.open(str(p)) as img:
                    # Thu nhỏ ảnh nếu kích thước lớn hơn 1024px để giảm payload, tránh timeout
                    if max(img.size) > 1024:
                        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                    data = base64.b64encode(buf.getvalue()).decode("utf-8")
                    return {"mime_type": "image/jpeg", "data": data}
            except Exception as pil_err:
                logger.debug(f"PIL compression fallback: {pil_err}")
                suffix = p.suffix.lower()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else None
                if not mime:
                    return None
                with open(str(p), "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {"mime_type": mime, "data": data}
        except Exception as e:
            logger.debug(f"Không thể encode ảnh {image_path}: {e}")
            return None

    def _call_vision_api(
        self, image_path: str, narration: str, visual_keyword: str,
        primary_entity: Optional[str] = None, scene_id: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Gọi Gemini Vision API để phân tích một bức ảnh so với lời thoại.
        Có xoay vòng key thông minh và thử qua danh sách VISION_MODELS.
        """
        if not self.rotator:
            return None

        img_obj = self._encode_image_base64(image_path)
        if not img_obj:
            return None

        entity_context = ""
        if primary_entity:
            entity_context = (
                f"\n- Nhân vật/thực thể chính của video: '{primary_entity}'. "
                f"Hình ảnh BẮT BUỘC phải thể hiện đúng nhân vật này hoặc bối cảnh liên quan trực tiếp. "
                f"Nếu ảnh chứa người hiện đại, diễn viên stock, hoặc hoàn toàn không liên quan "
                f"đến nhân vật lịch sử này → chấm entity_accuracy rất thấp (1-3)."
            )

        hook_context = ""
        if scene_id == 1:
            hook_context = (
                "\n- BẮT BUỘC ĐỐI VỚI CẢNH 1 (HOOK 3 GIÂY ĐẦU - QUYẾT ĐỊNH 90% RETENTION SHORTS):\n"
                "Ảnh Cảnh 1 PHẢI mang tính kích thích thị giác mạnh mẽ (Primal Visual Trigger / Shock Magnet) tùy theo chủ đề:\n"
                "  * Nếu chủ đề biển sâu/đại dương/bí ẩn: Phải có bóng quái vật khổng lồ/leviathan/thalassophobia hoặc vực thẳm rợn người.\n"
                "  * Nếu chủ đề phụ nữ/tình cảm/quyến rũ: Phải là hình tượng Femme Fatale quyến rũ bí ẩn, ánh mắt hút hồn nhìn qua vai, ánh sáng điện ảnh sang trọng.\n"
                "  * Nếu chủ đề kẻ nói dối/thao túng/tâm lý đen: Phải là cận cảnh cực đại (extreme macro) ánh mắt xoáy sâu, nụ cười nham hiểm hoặc bóng đen thì thầm.\n"
                "  * Nếu chủ đề tiền tài/quyền lực: Hầm vàng rực rỡ trong bóng tối, tiền bay, phong cách tỷ phú bóng đêm.\n"
                "  * TUYỆT ĐỐI KHÔNG ĐƯỢC CHẤP NHẬN: Ảnh đời thường tẻ nhạt, văn phòng làm việc chán ngắt, người ngồi gục mặt hoặc phong cảnh phẳng lặng.\n"
                "  * Nếu vi phạm (ảnh chán / thiếu năng lượng): BẮT BUỘC cho verdict = 'FAIL', visual_quality < 6.0 và trong trường 'suggestion' ghi rõ prompt Primal Hook Archetype để hệ thống tự động sinh lại ảnh mới ngay lập tức!"
            )

        prompt = f"""Bạn là chuyên gia thẩm định nội dung hình ảnh cho kênh YouTube Shorts chất lượng cao.

NHIỆM VỤ: Phân tích bức ảnh này và chấm điểm trên 4 tiêu chí (thang 1-10):

THÔNG TIN CẢNH:
- Cảnh số: {scene_id}
- Lời thoại (narration): "{narration}"
- Từ khoá hình ảnh (visual_keyword): "{visual_keyword}"
{entity_context}
{hook_context}

TIÊU CHÍ ĐÁNH GIÁ:
1. relevance (Độ liên quan): Ảnh có khớp với nội dung lời thoại và visual_keyword không?
   - 9-10: Ảnh minh họa chính xác nội dung câu chuyện kể
   - 7-8: Liên quan nhưng hơi chung chung
   - 4-6: Ảnh chỉ liên quan mơ hồ
   - 1-3: Ảnh hoàn toàn không liên quan hoặc sai ngữ cảnh

2. entity_accuracy (Độ chính xác thực thể): Nhân vật/sự vật có đúng không?
   - 9-10: Đúng nhân vật/sự kiện thực tế (ảnh tư liệu, chân dung lịch sử)
   - 7-8: Ảnh AI tái tạo hợp lý hoặc minh họa có bối cảnh đúng
   - 4-6: Nhân vật chung chung, không rõ ai
   - 1-3: Sai nhân vật hoàn toàn (ví dụ: ảnh stock người hiện đại cho nhân vật lịch sử)

3. historical_fidelity (Tính chân thật lịch sử/bối cảnh):
   - 9-10: Trang phục, kiến trúc, bối cảnh phù hợp thời đại
   - 7-8: Phần lớn phù hợp, có vài chi tiết hiện đại nhỏ
   - 4-6: Nhiều yếu tố lạc quẻ thời đại
   - 1-3: Hoàn toàn sai thời đại

4. visual_quality (Chất lượng hình ảnh & bố cục):
   - 9-10: Sắc nét, bố cục điện ảnh, màu sắc ấn tượng
   - 7-8: Chất lượng tốt, bố cục ổn
   - 4-6: Hơi mờ, bố cục kém
   - 1-3: Mờ nhòe, vỡ nét, chất lượng tệ

TRẢ VỀ DUY NHẤT một chuỗi JSON (không markdown, không giải thích):
{{
  "relevance": 8.5,
  "entity_accuracy": 7.0,
  "historical_fidelity": 8.0,
  "visual_quality": 9.0,
  "verdict": "PASS hoặc FAIL hoặc MARGINAL",
  "issues": ["Vấn đề cụ thể 1", "Vấn đề 2"],
  "suggestion": "Gợi ý từ khoá tìm ảnh thay thế nếu cần"
}}"""

        total_keys = max(len(self.rotator.keys), 1)
        max_attempts = max(total_keys * 2, 4)

        for attempt in range(max_attempts):
            key = self.rotator.get_key()
            if not key:
                break

            key_failed_quota = False
            for model in VISION_MODELS:
                try:
                    url = (
                        f"https://generativelanguage.googleapis.com/v1beta/"
                        f"models/{model}:generateContent?key={key}"
                    )
                    payload = {
                        "contents": [{
                            "parts": [
                                {"inline_data": {"mime_type": img_obj["mime_type"], "data": img_obj["data"]}},
                                {"text": prompt}
                            ]
                        }],
                        "generationConfig": {
                            "temperature": 0.2,
                            "maxOutputTokens": 600,
                        }
                    }
                    r = requests.post(url, json=payload, timeout=15)

                    if r.status_code in (429, 403):
                        logger.warning(f"ContentQualityCritic: Key bị giới hạn ({r.status_code}), xoay key...")
                        self.rotator.rotate_key()
                        key_failed_quota = True
                        break

                    if r.status_code == 503:
                        logger.warning(f"ContentQualityCritic: Model {model} quá tải (503)")
                        continue

                    if r.status_code != 200:
                        continue

                    data = r.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue

                    raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    raw = raw.strip()

                    if "```" in raw:
                        raw = re.sub(r"^```(?:json)?\s*", "", raw)
                        raw = re.sub(r"\s*```$", "", raw)

                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1:
                        raw = raw[start:end + 1]

                    parsed = json.loads(raw)
                    return parsed

                except json.JSONDecodeError:
                    logger.debug(f"ContentQualityCritic ({model}): Không parse được JSON từ response")
                    continue
                except requests.exceptions.Timeout:
                    logger.warning(f"ContentQualityCritic ({model}): Timeout khi gọi API, xoay key...")
                    self.rotator.rotate_key()
                    key_failed_quota = True
                    break
                except Exception as e:
                    logger.warning(f"ContentQualityCritic ({model}): Lỗi không xác định: {e}")
                    continue

            if not key_failed_quota:
                self.rotator.rotate_key()

        return None

    def _heuristic_score(
        self, scene: Dict[str, Any], primary_entity: Optional[str]
    ) -> Dict[str, Any]:
        """Chấm điểm dự phòng khi không gọi được Gemini Vision."""
        media_path = scene.get("media_path", "").lower()

        # Ảnh tư liệu/wiki → điểm cao
        if "_doc" in media_path or "_wiki" in media_path or "_real" in media_path:
            return {
                "relevance": 8.5, "entity_accuracy": 9.0,
                "historical_fidelity": 8.5, "visual_quality": 8.0,
                "verdict": "PASS", "issues": [],
                "suggestion": ""
            }
        # Ảnh AI → điểm khá
        if "_ai" in media_path or "_refined" in media_path:
            return {
                "relevance": 7.5, "entity_accuracy": 7.0,
                "historical_fidelity": 7.0, "visual_quality": 8.0,
                "verdict": "MARGINAL", "issues": ["Ảnh AI — cần xác minh tính chân thật"],
                "suggestion": ""
            }
        # Stock video → điểm thấp nếu có primary_entity
        if primary_entity and ("pexels" in media_path or "stock" in media_path):
            return {
                "relevance": 4.0, "entity_accuracy": 2.0,
                "historical_fidelity": 2.0, "visual_quality": 7.0,
                "verdict": "FAIL",
                "issues": [f"Stock video hiện đại cho nhân vật lịch sử '{primary_entity}'"],
                "suggestion": f"Tìm ảnh tư liệu hoặc AI portrait của {primary_entity}"
            }
        # Graphic placeholder → fail
        if "_graphic" in media_path:
            return {
                "relevance": 3.0, "entity_accuracy": 2.0,
                "historical_fidelity": 2.0, "visual_quality": 4.0,
                "verdict": "FAIL",
                "issues": ["Ảnh placeholder gradient — không phải nội dung thực"],
                "suggestion": scene.get("visual_keyword", "")
            }
        # Mặc định
        return {
            "relevance": 7.0, "entity_accuracy": 7.0,
            "historical_fidelity": 7.0, "visual_quality": 7.0,
            "verdict": "MARGINAL", "issues": [],
            "suggestion": ""
        }

    def evaluate_content_quality(
        self,
        scenes_media: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        max_ai_checks: int = 6,
    ) -> ContentQualityReport:
        """
        Chấm điểm nội dung toàn bộ kho tư liệu — kết hợp AI Vision + Heuristic.

        Args:
            scenes_media: Danh sách scene với media_path, visual_keyword, narration.
            primary_entity: Tên nhân vật/thực thể chính (nếu có).
            max_ai_checks: Số lượng scene tối đa dùng AI Vision (tiết kiệm quota).
        """
        scene_scores: List[SceneContentScore] = []
        failed_scene_ids: List[int] = []
        ai_checked = 0

        for item in scenes_media:
            scene_id = item.get("scene_id", 0)
            media_path = item.get("media_path", "")
            media_type = item.get("media_type", "image")
            visual_keyword = item.get("visual_keyword", "")
            narration = item.get("narration", "")

            # Chỉ phân tích AI cho ảnh (không cho video clip)
            use_ai = (
                media_type == "image"
                and ai_checked < max_ai_checks
                and Path(media_path).exists()
                and Path(media_path).stat().st_size > 2000
                and self.rotator is not None
            )

            if use_ai:
                result = self._call_vision_api(
                    media_path, narration, visual_keyword, primary_entity, scene_id=scene_id
                )
                if result:
                    ai_checked += 1
                else:
                    result = self._heuristic_score(item, primary_entity)
            else:
                result = self._heuristic_score(item, primary_entity)

            relevance = float(result.get("relevance", 7.0))
            entity_acc = float(result.get("entity_accuracy", 7.0))
            hist_fidelity = float(result.get("historical_fidelity", 7.0))
            vis_quality = float(result.get("visual_quality", 7.0))
            verdict = result.get("verdict", "MARGINAL")
            issues = result.get("issues", [])
            suggestion = result.get("suggestion", "")

            # Tính điểm tổng cho scene: Relevance 35%, Entity 30%, History 20%, Quality 15%
            scene_overall = (
                relevance * 0.35 +
                entity_acc * 0.30 +
                hist_fidelity * 0.20 +
                vis_quality * 0.15
            )

            if verdict == "FAIL" or scene_overall < 5.5:
                failed_scene_ids.append(scene_id)

            score = SceneContentScore(
                scene_id=scene_id,
                media_path=media_path,
                relevance_score=max(1.0, min(10.0, relevance)),
                entity_accuracy_score=max(1.0, min(10.0, entity_acc)),
                historical_fidelity_score=max(1.0, min(10.0, hist_fidelity)),
                visual_quality_score=max(1.0, min(10.0, vis_quality)),
                scene_overall=round(scene_overall, 1),
                verdict=verdict,
                issues=issues,
                replacement_suggestion=suggestion,
                ai_evaluated=use_ai and result.get("relevance") is not None,
            )
            scene_scores.append(score)

        # Tính điểm trung bình toàn bộ
        total = len(scene_scores)
        overall = (
            sum(s.scene_overall for s in scene_scores) / total
            if total > 0 else 0.0
        )
        passed = len(failed_scene_ids) == 0 and overall >= 6.5

        # Tạo summary chi tiết
        summary_lines = []
        for s in scene_scores:
            if s.issues:
                summary_lines.append(
                    f"Cảnh {s.scene_id} ({s.verdict}, {s.scene_overall}/10): "
                    f"{'; '.join(s.issues)}"
                )
        if not summary_lines:
            summary_text = (
                f"✅ Tất cả {total} cảnh đạt chuẩn nội dung. "
                f"Điểm trung bình: {overall:.1f}/10."
            )
        else:
            summary_text = "\n".join(summary_lines)

        return ContentQualityReport(
            scene_scores=scene_scores,
            overall_score=round(overall, 1),
            failed_scene_ids=list(set(failed_scene_ids)),
            passed=passed,
            ai_scenes_checked=ai_checked,
            summary=summary_text,
        )
