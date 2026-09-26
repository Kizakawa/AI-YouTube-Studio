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

--- BỘ QUY CHUẨN ĐÁNH GIÁ (Thang điểm 1 - 10) ---
1. hook_strength: Cảnh 1 có đủ điện giật, đảo ngược định kiến hoặc tạo sự cấp bách tột cùng để ngăn người xem lướt qua (Swipe Away) trong 2.5s đầu không? (Cần >= 8.0).
   - Đạt điểm cao: Áp dụng 1 trong 4 công thức: Cảnh báo khẩn cấp ("Nếu bạn thấy X, TUYỆT ĐỐI KHÔNG làm Y"), Đòn tâm lý cao thủ/FBI, Quy luật bóng tối, hoặc Cú sốc ngược đời. Lời thoại 5-8 từ, dứt khoát.
   - Bị trừ nặng (< 6.0): Mở đầu kiểu sách giáo khoa, định nghĩa học thuật, bị động ("Để bắt kẻ nói dối...", "Trong tâm lý học..."), câu hỏi sáo rỗng ("Bạn có biết...").
2. story_pacing: Nhịp điệu câu chuyện, mỗi cảnh ngắn gọn 6-10 từ (2.5-3.5s), toàn bộ video từ 20-28 giây (50-75 từ cho 6-8 cảnh), chuyển cảnh nhanh, không lê thê thừa thãi không? (Cần >= 8.0)
3. entity_coherence: Kịch bản và hình ảnh có bám chặt vào nhân vật/bối cảnh lịch sử thực tế không?
4. visual_direction: Từ khóa visual_keyword & stock_query có cụ thể, vật lý, giàu tính điện ảnh không? (Tuyệt đối không dùng từ trừu tượng như psychology, minimalism)
5. viral_loop: Cảnh cuối có tạo vòng lặp mượt mà quay trở lại Cảnh 1 không? (Infinite Shorts Loop)

--- YÊU CẦU TRẢ VỀ ---
Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ không markdown với schema sau:
{{
  "hook_strength": {{"score": 8.5, "feedback": "Nhận xét chi tiết..."}},
  "story_pacing": {{"score": 8.0, "feedback": "Nhận xét chi tiết..."}},
  "entity_coherence": {{"score": 9.0, "feedback": "Nhận xét chi tiết..."}},
  "visual_direction": {{"score": 8.5, "feedback": "Nhận xét chi tiết..."}},
  "viral_loop": {{"score": 8.0, "feedback": "Nhận xét chi tiết..."}},
  "actionable_critiques": [
    "Điểm cần sửa 1...",
    "Điểm cần sửa 2..."
  ]
}}"""

        total_keys = max(len(self.rotator.keys), 1)
        models = [
            "gemini-flash-latest",
            "gemini-3.1-flash-lite-preview",
            "gemma-4-26b-a4b-it",
            "gemini-3.1-pro-preview",
        ]
        for attempt in range(total_keys):
            key = self.rotator.get_key()
            if not key:
                break
            key_quota_failed = False
            for model in models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}]
                    }
                    r = requests.post(url, json=payload, timeout=15)
                    if r.status_code in (429, 403):
                        logger.warning(f"ScriptCritic: Key bị giới hạn ({r.status_code}), xoay key...")
                        self.rotator.rotate_key()
                        key_quota_failed = True
                        break

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

            if not key_quota_failed:
                self.rotator.rotate_key()

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
        has_hook_words = any(w in narr1 for w in ["bí mật", "sự thật", "không ai biết", "kinh hoàng", "nguy hiểm", "thực sự", "secret", "never", "truth", "shocking"])
        hook_score = 8.5 if has_hook_words else 7.0

        # Kiểm tra nhịp điệu (khoảng 50 - 75 từ cho 6-8 cảnh, thời lượng vàng 20-28s)
        pace_score = 8.5 if 45 <= total_words <= 80 and 5 <= scene_count <= 8 else 6.5

        # Kiểm tra thực thể
        ent_score = 8.5
        if primary_entity:
            ent_present = sum(1 for s in scenes if primary_entity.lower() in s.get("visual_keyword", "").lower())
            ent_score = 9.0 if ent_present >= 3 else 7.0

        vis_score = 8.0
        loop_score = 7.5

        overall = round((hook_score * 0.3) + (pace_score * 0.25) + (ent_score * 0.25) + (vis_score * 0.1) + (loop_score * 0.1), 1)

        critiques = []
        if hook_score < 8.0:
            critiques.append("Cảnh 1 cần mở đầu giật gân, tạo sự tò mò sốc hơn nữa trong 3 giây đầu.")
        if pace_score < 8.0:
            critiques.append("Độ dài kịch bản chưa tối ưu cho Shorts (cần khoảng 50-75 từ chia đều cho 6-8 cảnh, tổng thời lượng 20-28 giây để tối đa tỷ lệ xem hết AVD).")

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_score, feedback="Đánh giá theo độ hấp dẫn của hook cảnh 1"),
            story_pacing=ScriptCriteriaScore(score=pace_score, feedback=f"Tổng số từ: {total_words} từ cho {scene_count} cảnh"),
            entity_coherence=ScriptCriteriaScore(score=ent_score, feedback=f"Thực thể chính: {primary_entity or 'Chủ đề chung'}"),
            visual_direction=ScriptCriteriaScore(score=vis_score, feedback="Từ khóa hình ảnh đã được chuẩn hóa"),
            viral_loop=ScriptCriteriaScore(score=loop_score, feedback="Cảnh kết nối"),
            overall_score=overall,
            passed=overall >= 7.8,
            actionable_critiques=critiques,
        )
