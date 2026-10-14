"""
app.graph.evaluators.script_critic — Bộ chấm điểm & phản biện kịch bản tự động bằng Gemini.

Đánh giá 5 tiêu chí chuẩn hóa:
1. Hook Strength (Sức hút 3s đầu): 30%
2. Story Pacing & Flow (Nhịp điệu kể chuyện): 25%
3. Entity & Historical Coherence (Tính chân thật & thực thể): 25%
4. Visual Direction (Khả năng hiện thực hóa hình ảnh): 10%
5. Viral Shorts Loop (Vòng lặp giữ chân): 10%
"""
import json
import logging
import re
from typing import Dict, Any, Optional

import requests
from config.settings import settings
from app.graph.state import ScriptEvaluationReport, ScriptCriteriaScore
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)


class ScriptCritic:
    """
    Critic Agent độc lập đóng vai trò Giám đốc Nội dung (Content Director).
    Phản biện kịch bản một cách khắt khe để đảm bảo chất lượng triệu view cho YouTube Shorts.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def evaluate_script(
        self,
        script_data: Dict[str, Any],
        topic: str,
        channel_style: str = "general",
        primary_entity: Optional[str] = None
    ) -> ScriptEvaluationReport:
        """Chấm điểm kịch bản và trả về báo cáo chi tiết kèm hướng dẫn sửa chữa."""
        if not self.rotator:
            # Fallback nếu không có Gemini key: chấm điểm theo heuristic
            return self._heuristic_evaluate(script_data, primary_entity)

        key = self.rotator.get_key()
        if not key:
            return self._heuristic_evaluate(script_data, primary_entity)

        scenes = script_data.get("scenes", [])
        title = script_data.get("title", "")
        scenes_formatted = "\n".join([
            f"Scene {s.get('scene_id')}: [Visual: {s.get('visual_keyword')}] -> \"{s.get('narration')}\""
            for s in scenes
        ])

        entity_instruction = ""
        if primary_entity:
            entity_instruction = (
                f"- Thực thể/Nhân vật chính là '{primary_entity}'. "
                f"Kịch bản và từ khóa hình ảnh BẮT BUỘC phải bám sát nhân vật lịch sử này, "
                f"tuyệt đối không được chung chung hay dùng diễn viên hiện đại."
            )

        prompt = f"""Bạn là Giám đốc Sáng tạo và Trưởng ban Biên tập Kịch bản YouTube Shorts hàng đầu thế giới (chuẩn phong cách Vox, Magnates Media, Kurzgesagt).
Nhiệm vụ của bạn: Đóng vai trò Critic khắt khe, chấm điểm và phản biện kịch bản sau đây.

Chủ đề: {topic}
Tiêu đề dự kiến: {title}
Phong cách kênh: {channel_style}
{entity_instruction}

--- NỘI DUNG KỊCH BẢN ---
{scenes_formatted}

--- BỘ QUY CHUẨN ĐÁNH GIÁ THUẬT TOÁN SHORTS 5 HỒI CHIỀU SÂU (Thang điểm 1 - 10) ---
1. hook_strength (Mục tiêu: Viewed > 75%, Swipe-Away < 25%):
   - Cảnh 1 (5-8 từ, ~2.2s) có mở đầu bằng cảnh báo trực diện, nghịch lý tâm lý hoặc đối đầu sinh tử đủ sức khóa chặt ngón tay người xem không? (Cần >= 8.5).
   - Bị trừ nặng (< 5.0): Mở đầu sách giáo khoa, định nghĩa học thuật, chào hỏi sáo rỗng ("Bạn có biết...", "Did you know...").
2. story_pacing (Mục tiêu: Chuẩn Chiều Sâu 5 Hồi 32 - 42s, 88 - 118 từ, 8 - 9 phân cảnh trọn câu ~ 10 - 14 từ/cảnh):
   - BẮT BUỘC ĐỦ 5 HỒI CHIỀU SÂU:
     * Hồi 1 (Cảnh 1): Hook trực diện (5-8 từ).
     * Hồi 2 (Cảnh 2-3): Ví dụ câu nói đời thực cụ thể (trích dẫn nguyên văn câu nói ngoài đời của kẻ thao túng) HOẶC dẫn chứng lịch sử/khoa học xác thực.
     * Hồi 3 (Cảnh 4-5): Giải phẫu cơ chế thần kinh/tâm lý ngầm + Mid-Video Twist.
     * Hồi 4 (Cảnh 6-7): Đòn phản công / Cách hóa giải thực chiến từng lời nói hoặc hành động (giá trị cao khiến khán giả phải Lưu & Chia sẻ video).
     * Hồi 5 (Cảnh 8-9): Cú chốt & Cầu nối Vòng lặp Vô tận.
   - Bị trừ điểm (< 6.5): Kịch bản quá mỏng/chung chung (< 75 từ, thiếu ví dụ câu nói thật hoặc thiếu đòn phản công thực chiến), câu bị cắt đứt ngang giữa 2 cảnh, hoặc quá dài (> 128 từ). (Cần >= 8.0)
3. entity_coherence: Kịch bản và hình ảnh có bám chặt vào nhân vật/bối cảnh thực tế không?
4. visual_direction: Khả năng chỉ đạo hình ảnh & Chống ẩn dụ đạo cụ nghĩa đen (Anti-Literal-Metaphor):
   - Đạt điểm cao (>= 8.5): `visual_keyword` & `stock_query` mô tả con người thật, biểu cảm thật, tương tác thật (office, cafe, conversation, portrait, chess) hoặc động vật/tư liệu thật.
   - Bị trừ nặng (< 6.0): Dùng từ ngữ trừu tượng (psychology, success, power) hoặc dùng đạo cụ ẩn dụ nghĩa đen lạc đề (knife, blade, needles, velvet glove, puppet strings, cage) cho chủ đề tâm lý con người.
5. viral_loop (Mục tiêu: Seamless Infinite Loop đạt APV > 100% — CẤM câu kêu gọi Subscribe đọc thành tiếng ở cuối video):
   - Cảnh cuối (9-13 từ) BẮT BUỘC khép lại bằng cú chốt ấn tượng và nối mượt ý niệm quay trở lại câu Hook ở Cảnh 1, câu văn hoàn chỉnh ngữ pháp, KHÔNG chứa "Subscribe", "Leave a comment", "Đăng ký kênh".

--- YÊU CẦU TRẢ VỀ ---
Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ không markdown với schema sau:
{{
  "hook_strength": {{"score": 8.5, "feedback": "Nhận xét chi tiết về Hook Cảnh 1 (5-8 từ)..."}},
  "story_pacing": {{"score": 8.5, "feedback": "Nhận xét chi tiết về độ dày 5 hồi (88-118 từ, 8-9 cảnh trọn câu, có Ví dụ thật + Cơ chế + Đòn phản công)..."}},
  "entity_coherence": {{"score": 9.0, "feedback": "Nhận xét chi tiết..."}},
  "visual_direction": {{"score": 8.5, "feedback": "Nhận xét chi tiết về từ khóa hình ảnh con người/hành động cụ thể..."}},
  "viral_loop": {{"score": 8.5, "feedback": "Nhận xét chi tiết về cầu nối Seamless Infinite Loop..."}},
  "actionable_critiques": [
    "Bổ sung ví dụ câu nói cụ thể ngoài đời thực ở Cảnh 2-3...",
    "Bổ sung đòn phản công / cách đáp trả thực chiến ở Cảnh 6-7 để đạt chuẩn 88-118 từ (8-9 cảnh trọn câu)...",
    "Thay các từ khóa hình ảnh ẩn dụ đồ vật bằng biểu cảm và tương tác con người thật..."
  ]
}}"""

        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
        for model, key in self.rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}]
                }
                r = requests.post(url, json=payload, timeout=25)
                if r.status_code in (429, 403, 404, 503):
                    logger.warning(f"ScriptCritic: Model {model} trên key {key[:8]}... gặp mã {r.status_code}, chuyển ứng viên kế tiếp...")
                    self.rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=r.text[:250])
                    continue

                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
                        raw = raw.strip()
                        if "```" in raw:
                            raw = re.sub(r"^```(?:json)?\s*", "", raw)
                            raw = re.sub(r"\s*```$", "", raw)
                        start = raw.find("{")
                        end = raw.rfind("}")
                        if start != -1 and end != -1:
                            raw = raw[start:end+1]
                        parsed = json.loads(raw)
                        return self._build_report(parsed)
            except Exception as e:
                logger.warning(f"ScriptCritic ({model}) error: {e}")
                continue

        return self._heuristic_evaluate(script_data, primary_entity)

    def _build_report(self, parsed: Dict[str, Any]) -> ScriptEvaluationReport:
        h = parsed.get("hook_strength", {})
        p = parsed.get("story_pacing", {})
        e = parsed.get("entity_coherence", {})
        v = parsed.get("visual_direction", {})
        l = parsed.get("viral_loop", {})

        hook_s = float(h.get("score", 7.5))
        pace_s = float(p.get("score", 7.5))
        ent_s = float(e.get("score", 7.5))
        vis_s = float(v.get("score", 7.5))
        loop_s = float(l.get("score", 7.5))

        # Trọng số chuẩn hóa: Hook 30%, Pacing 25%, Entity 25%, Visual 10%, Loop 10%
        overall = (hook_s * 0.30) + (pace_s * 0.25) + (ent_s * 0.25) + (vis_s * 0.10) + (loop_s * 0.10)
        overall = round(overall, 1)

        critiques = parsed.get("actionable_critiques", [])
        passed = overall >= 8.0

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_s, feedback=h.get("feedback", "")),
            story_pacing=ScriptCriteriaScore(score=pace_s, feedback=p.get("feedback", "")),
            entity_coherence=ScriptCriteriaScore(score=ent_s, feedback=e.get("feedback", "")),
            visual_direction=ScriptCriteriaScore(score=vis_s, feedback=v.get("feedback", "")),
            viral_loop=ScriptCriteriaScore(score=loop_s, feedback=l.get("feedback", "")),
            overall_score=overall,
            passed=passed,
            actionable_critiques=critiques,
        )

    def _heuristic_evaluate(
        self, script_data: Dict[str, Any], primary_entity: Optional[str] = None
    ) -> ScriptEvaluationReport:
        """Đánh giá dự phòng bằng quy tắc thuật toán khi không thể gọi LLM."""
        scenes = script_data.get("scenes", [])
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)
        scene_count = len(scenes)

        # Kiểm tra Hook ở cảnh 1
        scene1 = scenes[0] if scenes else {}
        narr1 = scene1.get("narration", "").lower()
        words1 = narr1.split()
        has_hook_words = any(w in narr1 for w in [
            "bí mật", "sự thật", "không ai biết", "kinh hoàng", "nguy hiểm", "thực sự",
            "secret", "never", "truth", "shocking", "why", "if you", "swipe", "classified",
            "tại sao", "đừng", "vực sâu", "abyss", "terrified", "fear", "refused", "until",
            "moment", "signs", "what if", "vs", "only", "second", "realized", "stop", "trap",
            "blind", "silence", "weapon", "compliment"
        ])
        is_short_hook = len(words1) <= 10
        hook_score = 9.0 if (has_hook_words and is_short_hook) else (7.5 if has_hook_words or is_short_hook else 6.0)

        # Kiểm tra nhịp điệu & chiều sâu (75 - 128 từ cho 7-10 cảnh trọn câu, thời lượng vàng 32-42s)
        pace_score = 8.8 if 75 <= total_words <= 128 and 7 <= scene_count <= 10 else 6.5

        # Kiểm tra thực thể
        ent_score = 8.5
        if primary_entity:
            ent_present = sum(1 for s in scenes if primary_entity.lower() in s.get("visual_keyword", "").lower())
            ent_score = 9.0 if ent_present >= 2 else 7.5

        vis_score = 8.5

        # Kiểm tra vòng lặp vô tận (Seamless Loop) & KHÔNG dính câu xin Subscribe ở cảnh cuối
        last_scene = scenes[-1] if scenes else {}
        last_narr = last_scene.get("narration", "").strip().lower()
        has_bad_outro_cta = any(w in last_narr for w in ["subscribe", "inscreva-se", "đăng ký kênh", "leave a comment", "leave your theory"])
        has_loop_context = (not has_bad_outro_cta) and (len(last_narr.split()) >= 5 and last_narr[-1:] in (".", "!", "?"))
        loop_score = 8.8 if has_loop_context else 6.5

        overall = round((hook_score * 0.3) + (pace_score * 0.25) + (ent_score * 0.25) + (vis_score * 0.1) + (loop_score * 0.1), 1)

        critiques = []
        if hook_score < 8.0:
            critiques.append("Cảnh 1 cần mở đầu giật gân, tạo sự tò mò sốc hoặc tâm lý nghịch đảo trong 2.2 giây đầu (5-8 từ).")
        if pace_score < 8.0:
            critiques.append("Độ dày kịch bản chưa đạt chuẩn Chiều Sâu 5 Hồi (cần 88-118 từ chia đều cho 8-9 cảnh trọn câu 10-14 từ/cảnh, tổng thời lượng 32-42 giây, có Ví dụ đời thực + Cơ chế + Đòn phản công).")
        if loop_score < 8.0:
            critiques.append("Cảnh cuối không được đọc câu kêu gọi Subscribe làm tụt APV; cần kết nối ý niệm mượt mà quay trở lại Cảnh 1 (Seamless Loop).")

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_score, feedback="Đánh giá theo độ hấp dẫn của hook cảnh 1"),
            story_pacing=ScriptCriteriaScore(score=pace_score, feedback=f"Tổng số từ: {total_words} từ cho {scene_count} cảnh"),
            entity_coherence=ScriptCriteriaScore(score=ent_score, feedback=f"Thực thể chính: {primary_entity or 'Chủ đề chung'}"),
            visual_direction=ScriptCriteriaScore(score=vis_score, feedback="Từ khóa hình ảnh đã được chuẩn hóa"),
            viral_loop=ScriptCriteriaScore(score=loop_score, feedback="Cảnh kết nối"),
            overall_score=overall,
            passed=overall >= 8.0,  # [P1-FIX-3] Đồng nhất với ngưỡng AI evaluator (7.8 → 8.0)
            actionable_critiques=critiques,
            evaluated_by="heuristic",  # [P1-FIX-4] Đánh dấu là fallback không phải AI thật
        )
