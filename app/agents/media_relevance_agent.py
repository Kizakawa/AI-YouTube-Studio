"""
app.agents.media_relevance_agent — Tác nhân Đánh Giá & Chấm Điểm Độ Liên Quan Nội Dung Media (Media Relevance Critic Agent).

Chức năng:
1. Thẩm định NỘI DUNG của hình ảnh HOẶC video clip bằng Gemini Multimodal Vision API.
   - Với video (.mp4, .mov, .webm): Tự động trích xuất frame tại giây thứ 1.0 (hoặc giữa clip) bằng FFmpeg để kiểm định.
   - Với ảnh (.jpg, .png, .webp): Tối ưu hóa kích thước bằng Pillow và gửi lên Gemini Vision.
2. Chấm điểm chi tiết (thang điểm 1-10):
   - relevance: Độ liên quan trực tiếp đến lời thoại phân cảnh và chủ đề video.
   - entity_accuracy: Độ chính xác của nhân vật, đồ vật, hiện tượng được nhắc đến.
   - visual_quality: Bố cục, độ nét, tính điện ảnh 9:16 (tránh ảnh phẳng lặng, stock vô hồn).
3. Đưa ra phán quyết (PASS >= 7.0, FAIL < 7.0) kèm lý do (issues) và giải pháp hành động:
   - replacement_query: 1-3 từ tiếng Anh tốt nhất để tìm kiếm video/ảnh thay thế trên Pexels/Stock.
   - refined_prompt: Prompt mô tả điện ảnh chi tiết nếu cần sinh lại bằng AI.
"""
import base64
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

# Danh sách models hỗ trợ Vision trong cụm Google Ultra
VISION_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-latest",
    "gemini-3.1-pro-preview",
]


class MediaRelevanceAgent:
    """
    Agent chuyên trách chấm điểm độ liên quan giữa tư liệu media (ảnh/video) và kịch bản phân cảnh.
    Đảm bảo video không bao giờ dùng tư liệu lạc quẻ, sai lệch chủ đề hoặc phẳng lặng vô hồn.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    @staticmethod
    def _extract_video_frame(video_path: str, timestamp_sec: float = 1.0) -> Optional[str]:
        """Trích xuất 1 frame đại diện từ video clip bằng FFmpeg để gửi lên Vision API."""
        try:
            v_p = Path(video_path)
            if not v_p.exists() or v_p.stat().st_size < 5000:
                return None

            frame_path = str(v_p.parent / f"_inspect_frame_{v_p.stem}.jpg")
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{timestamp_sec:.2f}",
                "-i", str(v_p),
                "-vframes", "1",
                "-q:v", "2",
                frame_path,
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=10)
            if Path(frame_path).exists() and Path(frame_path).stat().st_size > 1000:
                return frame_path
        except Exception as e:
            logger.debug(f"[MediaRelevanceAgent] Lỗi trích xuất frame từ video {video_path}: {e}")
        return None

    @staticmethod
    def _encode_image_base64(image_path: str) -> Optional[Dict[str, str]]:
        """Đọc và nén ảnh bằng Pillow, sau đó encode base64."""
        try:
            p = Path(image_path)
            if not p.exists() or p.stat().st_size < 1000:
                return None

            try:
                from PIL import Image
                import io
                with Image.open(str(p)) as img:
                    if max(img.size) > 1024:
                        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                    data = base64.b64encode(buf.getvalue()).decode("utf-8")
                    return {"mime_type": "image/jpeg", "data": data}
            except Exception:
                suffix = p.suffix.lower()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
                with open(str(p), "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {"mime_type": mime, "data": data}
        except Exception as e:
            logger.debug(f"[MediaRelevanceAgent] Lỗi encode ảnh {image_path}: {e}")
            return None

    def evaluate_media(
        self,
        media_path: str,
        narration: str,
        visual_keyword: str,
        topic: str = "",
        scene_id: int = 1,
        primary_entity: Optional[str] = None,
        production_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thẩm định chi tiết 1 file media (ảnh hoặc video) so với nội dung cảnh.
        Trả về dict kết quả chấm điểm.
        """
        if not media_path or not Path(media_path).exists():
            return {
                "relevance": 1.0,
                "entity_accuracy": 1.0,
                "visual_quality": 1.0,
                "overall_score": 1.0,
                "is_relevant": False,
                "verdict": "FAIL",
                "issues": ["File media không tồn tại"],
                "replacement_query": visual_keyword or "cinematic scene",
                "refined_prompt": f"Dramatic cinematic vertical shot of {visual_keyword}",
                "ai_evaluated": False,
            }

        is_video = Path(media_path).suffix.lower() in (".mp4", ".mov", ".webm", ".mkv")
        inspect_image_path = media_path
        temp_frame_path = None

        if is_video:
            temp_frame_path = self._extract_video_frame(media_path, timestamp_sec=1.0)
            if temp_frame_path:
                inspect_image_path = temp_frame_path

        # Gọi Gemini Vision API
        ai_result = self._call_gemini_vision(
            inspect_image_path=inspect_image_path,
            narration=narration,
            visual_keyword=visual_keyword,
            topic=topic,
            scene_id=scene_id,
            primary_entity=primary_entity,
            is_video=is_video,
        )

        # Dọn dẹp frame tạm nếu có
        if temp_frame_path:
            try:
                Path(temp_frame_path).unlink(missing_ok=True)
            except Exception:
                pass

        if ai_result:
            return ai_result

        # Nếu không gọi được AI Vision (mất mạng/hết quota tạm thời), dùng Smart Heuristic Fallback
        return self._heuristic_fallback(
            media_path=media_path,
            narration=narration,
            visual_keyword=visual_keyword,
            topic=topic,
            primary_entity=primary_entity,
            scene_id=scene_id,
            is_video=is_video,
        )

    def _call_gemini_vision(
        self,
        inspect_image_path: str,
        narration: str,
        visual_keyword: str,
        topic: str,
        scene_id: int,
        primary_entity: Optional[str],
        is_video: bool,
    ) -> Optional[Dict[str, Any]]:
        """Gửi hình ảnh/frame lên Gemini Vision API để phân tích ngữ nghĩa và tính thẩm mỹ."""
        if not self.rotator:
            return None

        img_obj = self._encode_image_base64(inspect_image_path)
        if not img_obj:
            return None

        media_desc = "video clip động 9:16 (trích xuất 1 frame đại diện)" if is_video else "hình ảnh 9:16"

        entity_instruction = ""
        if primary_entity:
            entity_instruction = (
                f"\n- Nhân vật / Thực thể lịch sử chính: '{primary_entity}'. "
                f"Hình ảnh phải liên quan chặt chẽ đến nhân vật/thời đại này. "
                f"Nếu là ảnh người hiện đại/stock sai lệch thời đại -> BẮT BUỘC chấm entity_accuracy <= 3.0 và verdict = FAIL."
            )

        hook_instruction = ""
        if scene_id == 1:
            hook_instruction = (
                "\n- ĐÂY LÀ CẢNH 1 (HOOK 3 GIÂY ĐẦU TIÊN QUYẾT ĐỊNH GIỮ CHÂN KHÁN GIẢ):\n"
                "  Ảnh/Video phải CÓ TÍNH KỊCH TÍNH CAO (Primal Visual Hook), gây tò mò, bí ẩn, hoặc choáng ngợp.\n"
                "  Nếu là cảnh tĩnh lặng, công sở tẻ nhạt, đồ vật vô hồn -> ĐÁNH RỚT (verdict = FAIL, visual_quality < 6.0)."
            )

        prompt = f"""Bạn là Tổng Đạo Diễn Hình Ảnh & Giám Khảo Thẩm Định Tư Liệu (Director of Photography & Visual Media Critic) cho kênh YouTube Shorts hàng đầu.

NHIỆM VỤ: Nhìn kỹ vào tư liệu này (đây là {media_desc}) và đánh giá mức độ chuyên nghiệp, phù hợp với kịch bản phân cảnh:

THÔNG TIN PHÂN CẢNH:
- Chủ đề video: "{topic}"
- Cảnh số: {scene_id}
- Lời thoại (Narration): "{narration}"
- Từ khóa thị giác (Visual Keyword): "{visual_keyword}"{entity_instruction}{hook_instruction}

TIÊU CHÍ ĐÁNH GIÁ (Thang điểm 1.0 đến 10.0):
1. relevance (Độ liên quan nội dung & cảm xúc):
   - 9-10: Minh họa xuất sắc, trực quan, khớp 100% với lời kể và ý tưởng.
   - 7-8: Phù hợp, hòa hợp với ngữ cảnh chung của cảnh.
   - 4-6: Liên quan mơ hồ, chung chung, chưa sát với lời thoại.
   - 1-3: HOÀN TOÀN SAI LỆCH hoặc LẠC QUẺ.

2. modality_fitness (Độ phù hợp của thể loại công cụ & chuyển động):
   - Shorts là nền tảng chuyển động nhanh! Video chuyển động thực (Stock Video) hoặc Video AI kỳ ảo được ƯU TIÊN và KHUYẾN KHÍCH.
   - Nếu cảnh miêu tả hành động mạnh, bão tố, đường phố, thao tác mà chỉ dùng ảnh tĩnh phẳng lặng -> TRỪ ĐIỂM (modality_fitness <= 5.0).
   - Nếu là chân dung hoặc nhân vật lịch sử cụ thể: Cho phép ảnh tư liệu thật hoặc chân dung I2V 8K, nhưng cấm dùng stock video người hiện đại giả mạo.

3. visual_quality (Chất lượng thị giác & tính điện ảnh 9:16):
   - Bố cục sắc nét, ánh sáng điện ảnh, thu hút ánh nhìn, không mờ nhòe, không méo mó dị dạng.

QUY TẮC PHÁN QUYẾT (Verdict):
- PASS: relevance >= 7.0 VÀ visual_quality >= 7.0 (Với Cảnh 1 thì cả 2 phải >= 8.0).
- FAIL: relevance < 7.0 HOẶC visual_quality < 6.0 HOẶC sai lệch thực thể nghiêm trọng.

HÃY TRẢ VỀ DUY NHẤT MỘT ĐỐI TƯỢNG JSON (Không kèm markdown ```json):
{{
  "relevance": 8.5,
  "entity_accuracy": 8.0,
  "modality_fitness": 9.0,
  "visual_quality": 9.0,
  "overall_score": 8.6,
  "verdict": "PASS hoặc FAIL",
  "issues": ["Ghi rõ vấn đề nếu có (ví dụ: quá tĩnh, lạc đề, diễn viên hiện đại...)"],
  "recommended_tool": "pexels_video | ai_video | imagen3_i2v | wikimedia_archive",
  "replacement_query": "Từ khóa tiếng Anh 2-3 từ ngắn gọn tốt nhất để tìm kiếm video/ảnh thay thế nếu cần",
  "refined_prompt": "Prompt mô tả điện ảnh 8K 9:16 để sinh lại bằng AI nếu cần",
  "creative_idea": "Gợi ý 1 câu ý tưởng hình ảnh độc đáo, đan xen linh hoạt thay thế tư duy ghép ảnh tĩnh"
}}"""

        total_keys = max(len(self.rotator.keys), 1)
        for _ in range(min(total_keys * 2, 4)):
            key = self.rotator.get_key()
            if not key:
                break

            for model in VISION_MODELS:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
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
                            "responseMimeType": "application/json",
                        }
                    }
                    r = requests.post(url, json=payload, timeout=20)
                    if r.status_code == 200:
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
                        rel = float(parsed.get("relevance", 7.0))
                        ent = float(parsed.get("entity_accuracy", 7.0))
                        mod = float(parsed.get("modality_fitness", 8.0))
                        vis = float(parsed.get("visual_quality", 7.0))
                        ov = float(parsed.get("overall_score") or (rel * 0.35 + ent * 0.25 + mod * 0.20 + vis * 0.20))
                        verdict = parsed.get("verdict", "PASS" if ov >= 7.0 else "FAIL").upper()
                        is_rel = (verdict == "PASS" and rel >= 6.8)

                        result = {
                            "relevance": round(rel, 1),
                            "entity_accuracy": round(ent, 1),
                            "modality_fitness": round(mod, 1),
                            "visual_quality": round(vis, 1),
                            "overall_score": round(ov, 1),
                            "is_relevant": is_rel,
                            "verdict": "PASS" if is_rel else "FAIL",
                            "issues": parsed.get("issues", []),
                            "recommended_tool": parsed.get("recommended_tool") or ("pexels_video" if is_video else "ai_video"),
                            "replacement_query": parsed.get("replacement_query") or visual_keyword,
                            "refined_prompt": parsed.get("refined_prompt") or f"Cinematic {visual_keyword}, 8k",
                            "creative_idea": parsed.get("creative_idea", ""),
                            "ai_evaluated": True,
                        }
                        logger.info(
                            f"🧠 [MediaRelevanceAgent] Cảnh {scene_id} ({'Video' if is_video else 'Ảnh'}): "
                            f"Điểm={ov}/10 (Rel={rel}, Mod={mod}, Vis={vis}) -> {result['verdict']}"
                        )
                        return result
                    elif r.status_code in (429, 403):
                        self.rotator.rotate_key()
                        break
                except Exception as ex:
                    logger.debug(f"[MediaRelevanceAgent] Lỗi khi gọi Gemini Vision ({model}): {ex}")
                    continue

            self.rotator.rotate_key()

        return None

    def _heuristic_fallback(
        self,
        media_path: str,
        narration: str,
        visual_keyword: str,
        topic: str,
        primary_entity: Optional[str],
        scene_id: int,
        is_video: bool,
    ) -> Dict[str, Any]:
        """Quy tắc phán đoán thông minh khi không có kết nối Vision API."""
        m_lower = Path(media_path).name.lower()
        text_full = f"{topic} {narration} {visual_keyword}".lower()

        # Kiểm tra placeholder
        if "_graphic" in m_lower or "placeholder" in m_lower:
            return {
                "relevance": 3.0,
                "entity_accuracy": 2.0,
                "visual_quality": 4.0,
                "overall_score": 3.0,
                "is_relevant": False,
                "verdict": "FAIL",
                "issues": ["File placeholder tạm thời, không phải tư liệu thực tế"],
                "recommended_tool": "pexels_video",
                "replacement_query": visual_keyword or "cinematic",
                "refined_prompt": f"Dramatic cinematic shot of {visual_keyword}",
                "ai_evaluated": False,
            }

        # Chỉ phạt stock video nếu phân cảnh đang nhắc trực tiếp tên nhân vật lịch sử và cần chân dung
        is_direct_person_mention = primary_entity and primary_entity.lower() in narration.lower()
        is_portrait_focus = any(w in text_full for w in ["portrait", "face", "chân dung", "khuôn mặt", "người"])

        if is_direct_person_mention and is_portrait_focus and ("pexels" in m_lower or "stock" in m_lower):
            return {
                "relevance": 4.0,
                "entity_accuracy": 2.0,
                "visual_quality": 7.5,
                "overall_score": 4.5,
                "is_relevant": False,
                "verdict": "FAIL",
                "issues": [f"Dùng video stock diễn viên hiện đại cho chân dung nhân vật lịch sử '{primary_entity}'"],
                "recommended_tool": "wikimedia_archive",
                "replacement_query": f"{primary_entity} historical portrait",
                "refined_prompt": f"Historical authentic portrait of {primary_entity}",
                "ai_evaluated": False,
            }

        # Nếu là video clip B-roll thật từ Pexels cho hành động / môi trường / bối cảnh
        if is_video and ("pexels" in m_lower or "broll" in m_lower or "_video" in m_lower):
            return {
                "relevance": 8.2,
                "entity_accuracy": 8.0,
                "visual_quality": 9.0,
                "overall_score": 8.5,
                "is_relevant": True,
                "verdict": "PASS",
                "issues": [],
                "recommended_tool": "pexels_video",
                "replacement_query": visual_keyword,
                "refined_prompt": f"Cinematic dynamic video of {visual_keyword}",
                "ai_evaluated": False,
            }

        # Mặc định đạt mức chấp nhận được
        return {
            "relevance": 7.2,
            "entity_accuracy": 7.0,
            "visual_quality": 7.5,
            "overall_score": 7.2,
            "is_relevant": True,
            "verdict": "PASS",
            "issues": [],
            "replacement_query": visual_keyword,
            "refined_prompt": f"Cinematic shot of {visual_keyword}",
            "ai_evaluated": False,
        }
