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

--- BỘ QUY CHUẨN ĐÁNH GIÁ THUẬT TOÁN SHORTS (Thang điểm 1 - 10) ---
1. hook_strength (Mục tiêu: Viewed > 75%, Swipe-Away < 25% trong nhóm thử nghiệm 100-300 người):
   - Cảnh 1 có đủ sốc thị giác, đảo ngược định kiến hoặc tạo sự cấp bách tột cùng để khóa chặt ngón tay người xem trong 1.8s đầu không? (Cần >= 8.5).
   - Đạt điểm cao: Lời thoại 4-7 từ dứt khoát theo 4 công thức (Cảnh báo nguy hiểm, Đòn tâm lý cao thủ, Quy luật bóng tối, Cú sốc ngược đời).
   - Bị trừ nặng (< 5.0): Mở đầu sách giáo khoa, định nghĩa học thuật ("Để bắt kẻ nói dối...", "Trong tâm lý học..."), chào hỏi sáo rỗng ("Bạn có biết...", "Xin chào...").
2. story_pacing (Mục tiêu: Đạt thời lượng cô đọng 22 - 32s với nhịp cắt cảnh siêu tốc 1.8 - 2.5s/cảnh, KHÔNG DÀI LÊ THÊ và có Payoff sâu sắc để vượt trần 10k - 50k view):
   - Thời lượng vàng siêu tốc: 22 - 32 giây (60 - 95 từ, 8 - 11 phân cảnh nhanh, 6 - 9 từ/cảnh, 1.8 - 2.5s/cảnh).
   - YẾU TỐ PAYOFF BẮT BUỘC (Cảnh 5-8): Phải cung cấp giải pháp thực chiến, bản chất ngầm thực sự bị che giấu hoặc bài học đắt giá giải tỏa tò mò của Hook, tạo giá trị cao thôi thúc người xem Lưu (Save) và Chia sẻ (Share).
   - Cảnh 2, 3, 4 (giây 2-10) bắt buộc tung Micro-Hook / bằng chứng vật lý sốc để triệt tiêu cú rơi rụng (Drop-off cliff).
   - Bị trừ điểm (< 6.0): Kịch bản quá dài lê thê (> 105 từ / > 36s khiến người xem mất kiên nhẫn), quá ngắn (< 48 từ) thiếu Payoff, hoặc quá ít cảnh (< 7 cảnh làm nhịp cắt chậm). (Cần >= 8.0)
3. entity_coherence: Kịch bản và hình ảnh có bám chặt vào nhân vật/bối cảnh lịch sử thực tế không?
4. visual_direction: Khả năng chỉ đạo hình ảnh & Đan xen công cụ linh hoạt (Anti-Slideshow & Tool Matching):
   - Đạt điểm cao (>= 8.5): Từ khóa visual_keyword & stock_query có hành động vật lý cụ thể, giàu tính điện ảnh, định hướng rõ Video B-roll thực tế, Video AI kỳ ảo, hoặc ảnh tư liệu lịch sử.
   - Bị trừ nặng (< 6.0): Dùng từ ngữ trừu tượng (psychology, success, happiness, minimalism) khiến video biến thành slideshow ảnh tĩnh buồn ngủ.
5. viral_loop (Mục tiêu: Seamless Infinite Loop đạt APV > 110% — CẤM câu kêu gọi Subscribe đọc thành tiếng ở cuối video):
   - Cảnh cuối BẮT BUỘC phải khép lại bằng cú chốt Payoff ấn tượng và bắc cầu ý niệm nối mượt quay trở lại câu Hook ở Cảnh 1 (Seamless Infinite Loop), câu văn hoàn chỉnh ngữ pháp.
   - Đạt điểm cao (>= 8.5): Cảnh cuối ngắn gọn (6-8 từ), nối liền mạch vào Cảnh 1 và KHÔNG chứa các câu đuôi báo hiệu kết thúc như "Subscribe right now...", "Leave a comment...", "Inscreva-se...", "Đăng ký kênh...".
   - Bị trừ điểm (< 5.0): Cảnh cuối chứa câu xin Subscribe/Comment dài dòng làm tụt APV, hoặc câu kết bị cắt cụt ngữ pháp.

--- YÊU CẦU TRẢ VỀ ---
Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ không markdown với schema sau:
{{
  "hook_strength": {{"score": 8.5, "feedback": "Nhận xét chi tiết về khả năng đạt Viewed > 75% (Cảnh 1 ngắn gọn 4-7 từ)..."}},
  "story_pacing": {{"score": 8.5, "feedback": "Nhận xét chi tiết về nhịp cắt siêu tốc 8-11 cảnh (1.8-2.5s/cảnh, 60-95 từ) và yếu tố Payoff..."}},
  "entity_coherence": {{"score": 9.0, "feedback": "Nhận xét chi tiết..."}},
  "visual_direction": {{"score": 8.5, "feedback": "Nhận xét chi tiết về nhịp điệu thị giác và công cụ phù hợp..."}},
  "viral_loop": {{"score": 8.5, "feedback": "Nhận xét chi tiết về cầu nối Seamless Infinite Loop (không dính bẫy Subscribe outro)..."}},
  "actionable_critiques": [
    "Chỉ đạo viết lại Cảnh 1 (4-7 từ) để giật thumb-stopper đạt Viewed > 75%...",
    "Cắt gọn từ thừa, chia thành 8-10 phân cảnh siêu tốc (1.8-2.5s/cảnh, 6-9 từ/cảnh) và giữ cú bẻ khóa Payoff...",
    "Khử câu kêu gọi Subscribe ở cuối và thay bằng câu cầu nối Seamless Loop nối thẳng về Cảnh 1..."
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
            "blind", "silence"
        ])
        is_short_hook = len(words1) <= 8
        hook_score = 9.0 if (has_hook_words and is_short_hook) else (7.5 if has_hook_words or is_short_hook else 6.0)

        # Kiểm tra nhịp điệu (khoảng 50 - 108 từ cho 7-12 cảnh nhanh 1.8-2.5s/cảnh, thời lượng vàng 22-32s không dài lê thê)
        pace_score = 8.8 if 50 <= total_words <= 108 and 7 <= scene_count <= 12 else 6.5

        # Kiểm tra thực thể
        ent_score = 8.5
        if primary_entity:
            ent_present = sum(1 for s in scenes if primary_entity.lower() in s.get("visual_keyword", "").lower())
            ent_score = 9.0 if ent_present >= 3 else 7.0

        vis_score = 8.5

        # Kiểm tra vòng lặp vô tận (Seamless Loop) & KHÔNG dính câu xin Subscribe ở cảnh cuối
        last_scene = scenes[-1] if scenes else {}
        last_narr = last_scene.get("narration", "").strip().lower()
        has_bad_outro_cta = any(w in last_narr for w in ["subscribe", "inscreva-se", "đăng ký kênh", "leave a comment", "leave your theory"])
        has_loop_context = (not has_bad_outro_cta) and (len(last_narr.split()) >= 4 and last_narr[-1:] in (".", "!", "?"))
        loop_score = 8.8 if has_loop_context else 6.5

        overall = round((hook_score * 0.3) + (pace_score * 0.25) + (ent_score * 0.25) + (vis_score * 0.1) + (loop_score * 0.1), 1)

        critiques = []
        if hook_score < 8.0:
            critiques.append("Cảnh 1 cần mở đầu giật gân, tạo sự tò mò sốc hoặc tâm lý nghịch đảo trong 1.8 giây đầu (4-7 từ).")
        if pace_score < 8.0:
            critiques.append("Độ dài kịch bản chưa tối ưu cho Shorts (cần 60-95 từ chia đều cho 8-11 cảnh siêu tốc 1.8-2.5s/cảnh, tổng thời lượng 22-32 giây, không viết dài lê thê).")
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
