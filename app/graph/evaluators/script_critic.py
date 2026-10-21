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

    @classmethod
    def get_calibrated_thresholds(
        cls,
        channel_id: Optional[str] = None,
        channel_style: str = "general",
    ) -> Dict[str, Any]:
        """
        Hiệu chỉnh ngưỡng chấm Hook (`min_hook_score`) và Tổng điểm (`min_overall_score`)
        dựa trên tương quan với tỷ lệ vuốt bỏ thật (`swipe_away_pct`) của kênh trong DB:
        - Nếu kênh có tỷ lệ vuốt bỏ trung bình cao (swipe_away_pct >= 34%): siết ngưỡng Hook lên 8.5/10, Overall >= 8.2/10.
        - Nếu kênh có tỷ lệ vuốt bỏ trung bình vừa (30% <= swipe_away_pct < 34%): ngưỡng Hook >= 8.2/10, Overall >= 8.1/10.
        - Nếu kênh có tỷ lệ giữ chân tốt (swipe_away_pct < 30%): ngưỡng Hook >= 8.0/10, Overall >= 8.0/10.
        """
        avg_swipe = 31.0
        avg_stayed = 69.0
        avg_apv = 85.0
        winners = []
        losers = []
        canon_cid = ""

        try:
            from app.database.models import normalize_channel_id
            from app.services.video_library import get_weekly_winners_and_losers

            if channel_id:
                canon_cid = normalize_channel_id(channel_id)
            else:
                niche_low = (channel_style or "").strip().lower()
                for ch in settings.get_channels_config():
                    if str(ch.get("niche") or "").strip().lower() == niche_low:
                        canon_cid = ch.get("id", "")
                        break
            if canon_cid:
                perf = get_weekly_winners_and_losers(canon_cid, top_k=3)
                avg_swipe = float(perf.get("avg_swipe_away_pct", 31.0))
                avg_stayed = float(perf.get("avg_stayed_to_watch_pct", 69.0))
                avg_apv = float(perf.get("avg_view_percentage", 85.0))
                winners = perf.get("winners", [])
                losers = perf.get("losers", [])
        except Exception as e:
            logger.debug(f"[ScriptCritic] Không thể đọc dữ liệu swipe_away_pct cho {channel_id}: {e}")

        if avg_swipe >= 34.0:
            min_hook = 8.5
            min_overall = 8.2
        elif avg_swipe >= 30.0:
            min_hook = 8.2
            min_overall = 8.1
        else:
            min_hook = 8.0
            min_overall = 8.0

        return {
            "channel_id": canon_cid,
            "avg_swipe_away_pct": avg_swipe,
            "avg_stayed_to_watch_pct": avg_stayed,
            "avg_view_percentage": avg_apv,
            "min_hook_score": min_hook,
            "min_overall_score": min_overall,
            "winners": winners,
            "losers": losers,
        }

    @classmethod
    def _calibrate_hook_score_with_real_data(
        cls,
        raw_hook_score: float,
        script_data: Optional[Dict[str, Any]] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
        scene_1_narration: Optional[str] = None,
        title: Optional[str] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> tuple[float, list[str]]:
        """
        Kiểm định thực nghiệm câu Hook (Cảnh 1) dựa trên dữ liệu vuốt bỏ thật của kênh
        và công thức chiến thắng "A vs B" + câu khẳng định gây sốc của pt-BR / Wild Odyssey.
        Hỗ trợ cả 2 kiểu truyền tham số (script_data/calibration và scene_1_narration/title/thresholds).
        """
        extra_critiques: list[str] = []
        score = float(raw_hook_score)
        s_data = script_data or {}
        cal = calibration or thresholds or {"min_hook_score": 8.0, "min_overall_score": 8.0, "losers": []}
        scenes = s_data.get("scenes") or []
        eff_title = str(title if title is not None else (s_data.get("title") or "")).strip()
        if scene_1_narration is not None:
            s1_narr = str(scene_1_narration).strip()
        else:
            s1_narr = str(scenes[0].get("narration") or "").strip() if scenes and isinstance(scenes[0], dict) else ""
        combined = f"{eff_title} {s1_narr}".strip()

        words = re.findall(r"\b\w+\b", s1_narr, flags=re.UNICODE)
        w_cnt = len(words)

        # 1. Cấm câu hỏi Yes/No yếu ở Cảnh 1 (tương quan trực tiếp với Swipe-Away > 40%)
        weak_q = re.match(
            r"^(?:did you know|do you know|have you ever|are you|can you|would you|bạn có biết|você sabia)\b",
            s1_narr,
            re.IGNORECASE,
        )
        if weak_q:
            score = min(score, 5.5)
            extra_critiques.append(
                f"CHẶN HOOK: Cảnh 1 dùng câu hỏi Yes/No yếu ('{s1_narr}') gây tỷ lệ vuốt bỏ cao. "
                f"Bắt buộc đổi sang câu khẳng định gây sốc + thông số vật lý hoặc đối đầu 'A vs B'!"
            )

        # 2. Kiểm tra độ dài Cảnh 1 (3-18 từ để đọc trọn trong 2.0-3.5s đầu)
        if w_cnt > 20 or w_cnt < 3:
            score = max(1.0, score - 1.2)
            extra_critiques.append(
                f"Cảnh 1 có độ dài chưa chuẩn ({w_cnt} từ). Cần cô đọng 4-15 từ để khóa ngón tay người xem ngay giây đầu."
            )

        # 3. Đối chiếu chống lặp lại mô-típ của Bottom 3 Losers (video có swipe_away_pct cao nhất kênh)
        s1_tokens = {w.lower() for w in words if len(w) > 3}
        for loser in cal.get("losers", []):
            l_hook = str(loser.get("hook_text") or loser.get("title") or "")
            l_tokens = {w.lower() for w in re.findall(r"\b\w+\b", l_hook, flags=re.UNICODE) if len(w) > 3}
            if s1_tokens and l_tokens:
                overlap = len(s1_tokens & l_tokens) / max(1, min(len(s1_tokens), len(l_tokens)))
                if overlap >= 0.65 and len(s1_tokens & l_tokens) >= 4:
                    score = max(1.0, score - 2.0)
                    l_swp = loser.get("swipe_away_pct")
                    swp_label = f"{float(l_swp):.1f}%" if l_swp is not None else "cao"
                    extra_critiques.append(
                        f"Cảnh 1 lặp lại mô-típ của video thua cuộc có tỷ lệ vuốt bỏ {swp_label} ('{l_hook[:60]}'). Phải đổi góc tiếp cận hoàn toàn!"
                    )
                    break

        # 4. Thưởng/Phạt theo công thức "A vs B" & thông số định lượng gây sốc (EN & PT-BR)
        has_number = bool(re.search(r"\d", combined))
        has_confrontation_or_shock = bool(
            re.search(
                r"\b(?:vs\.?|versus|against|contra|never|stop|don't|refused|crushes|destroys|kills|deadly|impossible|instant|seconds|meters|feet|miles|psi|mph|nunca|jamais|mortal|impossível|segredo|erro|medo|foge|ataque|presa|abismo|oceano|profundidade|mordida|onça|jacaré|tubarão|baleia|lula|cachalote|cérebro|mente|sinal|silêncio|império|guerra|história|secret|truth|shocking|terrified|fear|escape|hunt|predator|jaw|bite|brain|mind|psychology|empire|war|history|ocean|abyss|deep)\b",
                combined,
                re.IGNORECASE | re.UNICODE,
            )
        )
        if (has_number or has_confrontation_or_shock) and 3 <= w_cnt <= 18:
            score = max(score, 8.6)
            if has_number and has_confrontation_or_shock:
                score = min(9.8, score + 0.4)
        elif not has_number and not has_confrontation_or_shock:
            score = max(1.0, score - 0.5)
            if score < cal.get("min_hook_score", 8.0):
                extra_critiques.append(
                    "Hook thiếu thông số định lượng cụ thể (con số, giây, mét, PSI, %) hoặc thế đối đầu trực diện 'A vs B' (Công thức pt-BR & Wild Odyssey)."
                )

        return round(score, 1), extra_critiques

    def evaluate_script(
        self,
        script_data: Dict[str, Any],
        topic: str,
        channel_style: str = "general",
        primary_entity: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> ScriptEvaluationReport:
        """Chấm điểm kịch bản theo dữ liệu giữ chân thật và trả về báo cáo chi tiết kèm hướng dẫn sửa chữa."""
        calibration = self.get_calibrated_thresholds(channel_id=channel_id, channel_style=channel_style)
        if not self.rotator:
            return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

        key = self.rotator.get_key()
        if not key:
            return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

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

        winners_block = ""
        if calibration.get("winners"):
            w_lines = [
                f"  + WINNER: \"{w['title']}\" (Hook: \"{w['hook_text']}\") -> {w['views']} views, Stayed: {w['stayed_to_watch_pct']}%, Swipe-Away: {w['swipe_away_pct']}%"
                for w in calibration["winners"][:3]
            ]
            winners_block += "\n- DỮ LIỆU THẬT 3 VIDEO THẮNG CỦA KÊNH:\n" + "\n".join(w_lines)
        if calibration.get("losers"):
            l_lines = [
                f"  - LOSER: \"{l['title']}\" (Hook: \"{l['hook_text']}\") -> {l['views']} views, Swipe-Away: {l['swipe_away_pct']}% (CẤM LẶP LẠI MÔ-TÍP NÀY)"
                for l in calibration["losers"][:3]
            ]
            winners_block += "\n- DỮ LIỆU THẬT 3 VIDEO THUA CỦA KÊNH:\n" + "\n".join(l_lines)

        min_hook_req = calibration.get("min_hook_score", 8.2)
        avg_swipe_pct = calibration.get("avg_swipe_away_pct", 31.0)

        prompt = f"""Bạn là Giám đốc Sáng tạo và Trưởng ban Biên tập Kịch bản YouTube Shorts hàng đầu thế giới (chuẩn phong cách Vox, Magnates Media, Kurzgesagt).
Nhiệm vụ của bạn: Đóng vai trò Critic khắt khe, chấm điểm và phản biện kịch bản sau đây dựa trên DỮ LIỆU GIỮ CHÂN THẬT của kênh.

Chủ đề: {topic}
Tiêu đề dự kiến: {title}
Phong cách kênh: {channel_style}
Tỷ lệ vuốt bỏ trung bình hiện tại của kênh (Real Swipe-Away Rate): {avg_swipe_pct:.1f}% -> Ngưỡng bắt buộc của hook_strength >= {min_hook_req}/10.
{entity_instruction}{winners_block}

--- NỘI DUNG KỊCH BẢN ---
{scenes_formatted}

--- BỘ QUY CHUẨN ĐÁNH GIÁ KỊCH BẢN SHORTS 5 HỒI ĐẦY ĐẶN, MƯỢT MÀ (Thang điểm 1 - 10) ---
1. hook_strength (Ngưỡng chặn cứng >= {min_hook_req}/10 — Mục tiêu: Viewed > 75%, Swipe-Away < 25%):
   - Cảnh 1 (5-9 từ) BẮT BUỘC mở đầu bằng thế đối đầu trực diện ("A vs B") HOẶC câu khẳng định/cảnh báo sắc bén.
   - Bị đánh trượt (< 6.0): Mở đầu bằng câu hỏi Yes/No ("Did you know...", "Have you ever..."), định nghĩa sách giáo khoa, hoặc lặp lại cấu trúc của 3 video thua cuộc (Losers).
2. story_pacing (Mục tiêu: Mạch truyện đầy đặn, mượt mà, có đầu có đuôi 30 - 40s, 85 - 120 từ, 7 - 9 phân cảnh — TUYỆT ĐỐI KHÔNG CỤT LỦN):
   - Đánh giá cao (>= 8.5) kịch bản dẫn dắt tự nhiên, có câu chuyển ý mượt mà, giải thích thấu đáo đủ 5 Hồi (Hook -> Bối cảnh/Ví dụ thật -> Giải thích cơ chế -> Cao trào/Đòn hóa giải -> Kết luận trọn vẹn).
   - Trừ điểm nếu kịch bản bị viết cụt lủn, nhảy cóc ý, hoặc cắt câu máy móc.
3. entity_coherence: Nội dung kịch bản bám sát chủ đề và nhân vật/vấn đề chính một cách tự nhiên.
4. visual_direction: Chỉ cần từ khóa hình ảnh (`visual_keyword` & `stock_query`) phù hợp tương đối với chủ đề chung của kịch bản là đạt điểm cao (>= 8.5) — KHÔNG đòi hỏi khóa cứng từ khóa theo từng câu thoại nhỏ.
5. viral_loop (CẤM câu kêu gọi Subscribe đọc thành tiếng ở cuối video):
   - Cảnh cuối khép lại câu chuyện một cách trọn vẹn, có chiều sâu và nối tự nhiên về ý mở đầu ở Cảnh 1.

--- YÊU CẦU TRẢ VỀ ---
Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ không markdown với schema sau:
{{
  "hook_strength": {{"score": 8.5, "feedback": "Nhận xét chi tiết về Hook Cảnh 1..."}},
  "story_pacing": {{"score": 8.8, "feedback": "Nhận xét về độ mượt mà, đầy đặn có đầu có đuôi của mạch truyện 5 hồi (85-120 từ)..."}},
  "entity_coherence": {{"score": 9.0, "feedback": "Nhận xét chi tiết..."}},
  "visual_direction": {{"score": 8.8, "feedback": "Nhận xét tương đối về bối cảnh hình ảnh chung..."}},
  "viral_loop": {{"score": 8.8, "feedback": "Nhận xét chi tiết về câu kết trọn vẹn & cầu nối Loop..."}},
  "actionable_critiques": [
    "Nêu rõ cách cải thiện nếu Hook Cảnh 1 yếu hoặc nếu kịch bản bị cụt lủn..."
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
                        return self._build_report(parsed, script_data=script_data, calibration=calibration, channel_style=channel_style)
            except Exception as e:
                logger.warning(f"ScriptCritic ({model}) error: {e}")
                continue

        return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

    def _build_report(
        self,
        parsed: Dict[str, Any],
        script_data: Optional[Dict[str, Any]] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
    ) -> ScriptEvaluationReport:
        cal = calibration or {"min_hook_score": 8.0, "min_overall_score": 8.0, "losers": []}
        h = parsed.get("hook_strength", {})
        p = parsed.get("story_pacing", {})
        e = parsed.get("entity_coherence", {})
        v = parsed.get("visual_direction", {})
        l = parsed.get("viral_loop", {})

        raw_hook_s = float(h.get("score", 7.5))
        hook_s, hook_extra_critiques = self._calibrate_hook_score_with_real_data(
            raw_hook_score=raw_hook_s,
            script_data=script_data or {},
            calibration=cal,
            channel_style=channel_style,
        )
        pace_s = float(p.get("score", 7.5))
        ent_s = float(e.get("score", 7.5))
        vis_s = float(v.get("score", 7.5))
        loop_s = float(l.get("score", 7.5))

        # Trọng số chuẩn hóa: Hook 30%, Pacing 25%, Entity 25%, Visual 10%, Loop 10%
        overall = (hook_s * 0.30) + (pace_s * 0.25) + (ent_s * 0.25) + (vis_s * 0.10) + (loop_s * 0.10)
        overall = round(overall, 1)

        critiques = list(parsed.get("actionable_critiques", []) or [])
        for hc in reversed(hook_extra_critiques):
            if hc not in critiques:
                critiques.insert(0, hc)

        min_hook = float(cal.get("min_hook_score", 8.0))
        min_overall = float(cal.get("min_overall_score", 8.0))
        # CHẶN CỨNG: Bắt buộc cả tổng điểm VÀ điểm Hook Cảnh 1 đều phải vượt qua ngưỡng hiệu chỉnh từ dữ liệu thật!
        passed = (overall >= min_overall) and (hook_s >= min_hook)
        if not passed and hook_s < min_hook:
            msg = f"Hook Cảnh 1 ({hook_s:.1f}/10) chưa đạt ngưỡng chống vuốt bỏ của kênh ({min_hook:.1f}/10 — Swipe-Away TB: {cal.get('avg_swipe_away_pct', 31.0):.1f}%)."
            if msg not in critiques:
                critiques.insert(0, msg)

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
        self,
        script_data: Dict[str, Any],
        primary_entity: Optional[str] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
    ) -> ScriptEvaluationReport:
        """Đánh giá dự phòng bằng quy tắc thuật toán khi không thể gọi LLM, áp dụng cùng bộ lọc dữ liệu thật."""
        cal = calibration or self.get_calibrated_thresholds(channel_style=channel_style)
        scenes = script_data.get("scenes", [])
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)
        scene_count = len(scenes)

        # Kiểm tra Hook ở cảnh 1
        scene1 = scenes[0] if scenes else {}
        narr1 = scene1.get("narration", "").lower()
        words1 = narr1.split()
        is_short_hook = 3 <= len(words1) <= 18
        raw_hook_score = 8.8 if is_short_hook else 7.6
        hook_score, hook_extra = self._calibrate_hook_score_with_real_data(
            raw_hook_score=raw_hook_score,
            script_data=script_data,
            calibration=cal,
            channel_style=channel_style,
        )

        # Kiểm tra nhịp điệu & độ đầy đặn của mạch truyện (55 - 155 từ cho 5-12 cảnh trọn câu, chống kịch bản cụt lủn)
        pace_score = 8.8 if 55 <= total_words <= 155 and 5 <= scene_count <= 12 else 6.8

        # Kiểm tra thực thể tương đối trên tổng thể kịch bản (không khóa cứng theo visual_keyword từng cảnh)
        ent_score = 8.8
        if primary_entity:
            full_narr_lower = " ".join(s.get("narration", "") for s in scenes).lower()
            ent_score = 9.0 if primary_entity.lower() in full_narr_lower else 8.5

        vis_score = 8.8

        # Kiểm tra vòng lặp vô tận (Seamless Loop) & KHÔNG dính câu xin Subscribe ở cảnh cuối
        last_scene = scenes[-1] if scenes else {}
        last_narr = last_scene.get("narration", "").strip().lower()
        has_bad_outro_cta = any(w in last_narr for w in ["subscribe", "inscreva-se", "đăng ký kênh", "leave a comment", "leave your theory"])
        has_loop_context = (not has_bad_outro_cta) and (len(last_narr.split()) >= 5 and last_narr[-1:] in (".", "!", "?"))
        loop_score = 8.8 if has_loop_context else 6.5

        overall = round((hook_score * 0.3) + (pace_score * 0.25) + (ent_score * 0.25) + (vis_score * 0.1) + (loop_score * 0.1), 1)

        min_hook = float(cal.get("min_hook_score", 8.0))
        min_overall = float(cal.get("min_overall_score", 8.0))
        critiques = list(hook_extra)
        if hook_score < min_hook:
            critiques.append(f"Cảnh 1 ({hook_score:.1f}/10) cần mở đầu giật gân theo công thức 'A vs B' hoặc câu khẳng định số liệu gây sốc (ngưỡng kênh >= {min_hook:.1f}/10).")
        if pace_score < 8.0:
            critiques.append("Độ dày kịch bản chưa đạt chuẩn Chiều Sâu 5 Hồi (cần 88-118 từ chia đều cho 8-9 cảnh trọn câu 10-14 từ/cảnh, tổng thời lượng 32-42 giây).")
        if loop_score < 8.0:
            critiques.append("Cảnh cuối không được đọc câu kêu gọi Subscribe làm tụt APV; cần kết nối ý niệm mượt mà quay trở lại Cảnh 1 (Seamless Loop).")

        passed = (overall >= min_overall) and (hook_score >= min_hook)

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_score, feedback="Đánh giá theo tương quan tỷ lệ vuốt bỏ thật của kênh"),
            story_pacing=ScriptCriteriaScore(score=pace_score, feedback=f"Tổng số từ: {total_words} từ cho {scene_count} cảnh"),
            entity_coherence=ScriptCriteriaScore(score=ent_score, feedback=f"Thực thể chính: {primary_entity or 'Chủ đề chung'}"),
            visual_direction=ScriptCriteriaScore(score=vis_score, feedback="Từ khóa hình ảnh đã được chuẩn hóa"),
            viral_loop=ScriptCriteriaScore(score=loop_score, feedback="Cảnh kết nối"),
            overall_score=overall,
            passed=passed,
            actionable_critiques=critiques,
            evaluated_by="heuristic",
        )

