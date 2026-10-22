"""
app.services.quality_evaluator — Hệ thống Đánh giá & Chấm điểm 2 lớp (Script & Video QA).
Đảm bảo chất lượng nội dung đạt chuẩn triệu view YouTube Shorts trước khi dựng và đăng bài.
"""
import os
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.utils.ffmpeg_helper import probe_file

logger = logging.getLogger(__name__)


class QualityEvaluator:
    """Bộ chấm điểm kịch bản và video thành phẩm trước khi xuất bản."""

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    # ═════════════════════════════════════════════════════════════════════════════
    # 1. ĐÁNH GIÁ & CHẤM ĐIỂM KỊCH BẢN (SCRIPT QUALITY GATE)
    # ═════════════════════════════════════════════════════════════════════════════

    def evaluate_script(
        self,
        script_dict: Dict[str, Any],
        topic: str,
        channel_niche: str,
        language: str = "pt-BR",
        channel_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Đánh giá kịch bản theo 4 tiêu chí cốt lõi + hiệu chỉnh ngưỡng Hook theo tỷ lệ vuốt bỏ thật (swipe_away_pct):
        1. Hook Strength (0-3s thu hút người xem ngay lập tức) - 30%
        2. Channel Niche Alignment (đúng ngách & phong cách kênh) - 25%
        3. Language Authenticity (chuẩn ngôn ngữ bản địa, không lai tạp) - 25%
        4. Retention Arc & Pacing (nhịp độ, cao trào, Seamless Loop) - 20%
        """
        title = script_dict.get("title", "")
        scenes = script_dict.get("scenes", [])
        full_text = " ".join([s.get("narration", "") for s in scenes])

        from app.graph.evaluators.script_critic import ScriptCritic
        cal_thresh = ScriptCritic.get_calibrated_thresholds(channel_id=channel_id, channel_style=channel_niche)
        min_hook = float(cal_thresh.get("min_hook_score", 8.0))
        min_total = int(round(float(cal_thresh.get("min_overall_score", 8.0)) * 10))

        # Cố gắng chấm điểm bằng Gemini AI
        report = self._evaluate_script_with_ai(
            title=title,
            scenes=scenes,
            full_text=full_text,
            topic=topic,
            channel_niche=channel_niche,
            language=language
        )
        if not report:
            # Fallback: Chấm điểm dựa trên hệ thống luật chuyên sâu (Rule-Based Heuristic)
            report = self._heuristic_evaluate_script(
                title=title,
                scenes=scenes,
                full_text=full_text,
                topic=topic,
                channel_niche=channel_niche,
                language=language
            )

        # Hiệu chỉnh điểm Hook theo dữ liệu vuốt bỏ thật (swipe_away_pct) và công thức pt-BR / Wild Odyssey
        scene_1_text = scenes[0].get("narration", "") if scenes else script_dict.get("hook", "")
        raw_hook = float(report.get("hook_score", 8.0))
        cal_hook, cal_notes = ScriptCritic._calibrate_hook_score_with_real_data(
            raw_hook_score=raw_hook,
            scene_1_narration=scene_1_text,
            title=title,
            channel_style=channel_niche,
            thresholds=cal_thresh,
        )
        report["hook_score"] = round(cal_hook, 1)
        n_s = float(report.get("niche_score", 8.0))
        l_s = float(report.get("language_score", 8.5))
        r_s = float(report.get("retention_score", 8.0))
        recalc_total = int(round((cal_hook * 0.35 + n_s * 0.25 + l_s * 0.20 + r_s * 0.20) * 10))
        report["score"] = recalc_total
        report["min_hook_required"] = min_hook
        report["min_score_required"] = min_total
        report["avg_swipe_away_pct"] = cal_thresh.get("avg_swipe_away_pct")

        weaknesses = list(report.get("weaknesses") or [])
        for note in cal_notes:
            if note.startswith("❌") or note.startswith("⚠️"):
                weaknesses.append(note)
        report["weaknesses"] = weaknesses

        passed = (recalc_total >= min_total) and (cal_hook >= min_hook)
        report["passed"] = passed
        report["verdict"] = "APROVADO (ĐẠT CHUẨN)" if passed else "REPROVADO (CHẶN XUẤT BẢN)"
        if not passed and cal_hook < min_hook:
            report["feedback"] = (
                f"Hook Cảnh 1 ({cal_hook:.1f}/10) chưa đạt ngưỡng chống vuốt bỏ ({min_hook:.1f}/10, "
                f"swipe_away={cal_thresh.get('avg_swipe_away_pct')}%). Cần công thức 'A vs B' hoặc câu khẳng định gây sốc có số liệu."
            )
        return report

    def _evaluate_script_with_ai(
        self,
        title: str,
        scenes: List[Dict[str, Any]],
        full_text: str,
        topic: str,
        channel_niche: str,
        language: str
    ) -> Optional[Dict[str, Any]]:
        if not self.rotator:
            return None

        key = self.rotator.get_key()
        if not key:
            return None

        scenes_txt = "\n".join([
            f"Scene {s.get('scene_id', i+1)}: {s.get('narration', '')}"
            for i, s in enumerate(scenes)
        ])

        is_pt = str(language).lower().startswith("pt")
        if is_pt:
            prompt = f"""Você é o Diretor Criativo e Especialista em Algoritmos do YouTube Shorts para o mercado do Brasil.
Avalie criticamente este roteiro de vídeo curto (Shorts).

Canal / Nicho: {channel_niche}
Idioma: {language} (Português do Brasil)
Tópico: {topic}
Título: {title}

ROTEIRO:
{scenes_txt}

Critérios de Avaliação (Notas de 0 a 10):
1. hook_score: O gancho dos primeiros 3 segundos (Cena 1) usa confronto "A vs B" ou afirmação de choque com dados reais e impede o swipe?
2. niche_score: O conteúdo está 100% alinhado com o nicho ({channel_niche})?
3. language_score: O português é 100% brasileiro, coloquial, natural, sem erros e SEM nenhuma palavra em inglês?
4. retention_score: Ritmo rápido, tensão crescente, clímax dramático e loop perfeito no final?

Responda APENAS em JSON válido no formato:
{{
  "hook_score": 8.5,
  "niche_score": 9.0,
  "language_score": 9.0,
  "retention_score": 8.5,
  "total_score": 88,
  "verdict": "APROVADO",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "critique": "Resumo da avaliação em 2 frases"
}}
"""
        else:
            prompt = f"""You are the Creative Director and YouTube Shorts Algorithm Specialist for high-retention content.
Critically evaluate this YouTube Shorts script.

Channel / Niche: {channel_niche}
Language: {language}
Topic: {topic}
Title: {title}

SCRIPT:
{scenes_txt}

Evaluation Criteria (Scores 0 to 10):
1. hook_score: Does Scene 1 use an electrifying 3-second hook ("A vs B" confrontation or shocking declarative claim with hard physical numbers, NO passive Yes/No questions) that stops the swipe?
2. niche_score: Is the content 100% aligned with the niche ({channel_niche})?
3. language_score: Is the language natural, engaging, native-level phrasing without awkward artifacts?
4. retention_score: Fast pacing, escalating tension, dramatic climax, and seamless infinite loop bridge?

Respond ONLY in valid JSON:
{{
  "hook_score": 8.5,
  "niche_score": 9.0,
  "language_score": 9.0,
  "retention_score": 8.5,
  "total_score": 88,
  "verdict": "APPROVED",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "critique": "Summary critique in 2 sentences"
}}
"""
        import requests
        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
        rotator = self.rotator
        headers = {"Content-Type": "application/json"}
        for model, k_use in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=6):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k_use}"
                gen_cfg = {"temperature": 0.2}
                if not model.startswith("gemma-"):
                    gen_cfg["responseMimeType"] = "application/json"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg
                }
                req_timeout = 14 if model.startswith("gemma-") else 20
                resp = requests.post(url, headers=headers, json=payload, timeout=req_timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    s_idx, e_idx = content.find("{"), content.rfind("}")
                    if s_idx != -1 and e_idx != -1:
                        content = content[s_idx:e_idx + 1]
                    result = json.loads(content)
                    total = int(result.get("total_score", 0))
                    if total == 0:
                        h = float(result.get("hook_score", 8.0))
                        n = float(result.get("niche_score", 8.0))
                        l = float(result.get("language_score", 8.0))
                        r = float(result.get("retention_score", 8.0))
                        total = int((h * 3.0 + n * 2.5 + l * 2.5 + r * 2.0) * 10 / 10)
                    passed = total >= 80
                    return {
                        "passed": passed,
                        "score": total,
                        "hook_score": float(result.get("hook_score", 8.0)),
                        "niche_score": float(result.get("niche_score", 8.0)),
                        "language_score": float(result.get("language_score", 8.0)),
                        "retention_score": float(result.get("retention_score", 8.0)),
                        "verdict": "APROVADO (ĐẠT)" if passed else "REPROVADO (CẦN TỐI ƯU)",
                        "feedback": result.get("critique", "Roteiro analisado por IA."),
                        "strengths": result.get("strengths", []),
                        "weaknesses": result.get("weaknesses", []),
                    }
                elif resp.status_code in (400, 429, 403, 404, 500, 502, 503, 504):
                    rotator.mark_model_rate_limited(k_use, model, status_code=resp.status_code, error_text=resp.text[:250])
                    continue
            except Exception as e:
                logger.warning(f"[QualityEvaluator] Lỗi gọi Gemini ({model}) chấm điểm kịch bản: {e}")
                rotator.mark_model_rate_limited(k_use, model, status_code=503, error_text=str(e))
        return None

    def _heuristic_evaluate_script(
        self,
        title: str,
        scenes: List[Dict[str, Any]],
        full_text: str,
        topic: str,
        channel_niche: str,
        language: str
    ) -> Dict[str, Any]:
        """Quy tắc thẩm định kịch bản dựa trên phân tích ngôn ngữ học & cấu trúc Shorts."""
        is_pt = str(language).lower().startswith("pt")
        hook_score = 8.5
        niche_score = 8.6
        lang_score = 9.0
        retention_score = 8.5
        issues = []
        strengths = []

        # 1. Kiểm tra Cảnh 1 (Hook 0-3s)
        if scenes:
            scene_1 = scenes[0].get("narration", "")
            words_s1 = scene_1.split()
            if len(words_s1) < 3:
                hook_score -= 2.0
                issues.append("Cảnh 1 quá ngắn, chưa đủ thông tin làm mồi câu (Hook).")
            elif len(words_s1) > 22:
                hook_score -= 1.5
                issues.append("Cảnh 1 quá dài (>22 từ), nhịp điệu mở đầu bị chậm dễ gây vuốt bỏ.")
            else:
                strengths.append(f"Cảnh 1 có độ dài lý tưởng ({len(words_s1)} từ) cho Hook 3s.")

            hook_cues = ["!", "vs", "versus", "contra", "never", "nunca", "jamais", "deadliest", "mortal", "crushes", "destroys", "psi", "meters", "metros", "feet", "seconds", "segundos"]
            if any(p in scene_1.lower() for p in hook_cues) or bool(re.search(r"\d", scene_1)):
                hook_score = min(9.8, hook_score + 0.6)
                strengths.append("Cảnh 1 có yếu tố đối đầu kịch tính hoặc khẳng định số liệu gây sốc.")

        # 2. Kiểm tra Niche fit & Chặn rò rỉ lệch ngách (Off-Niche Contamination)
        niche_kw_map = {
            "wildlife_predators": ["onça", "mordida", "predador", "selva", "anaconda", "feroz", "caça", "animal", "sucuri", "jacaré"] if is_pt else ["predator", "hunt", "bite", "kill", "lethal", "jaw", "beast", "wild", "lion", "tiger", "bear", "jaguar", "eagle", "attack", "speed", "orca", "shark", "anaconda", "caiman", "badger"],
            "deep_sea_earth_mysteries": ["mistério", "oceano", "profundidade", "abismo", "ciência", "criatura", "olhos", "silêncio"] if is_pt else ["mystery", "ocean", "deep", "abyss", "creature", "unexplained", "strange", "bizarre", "scientists", "sea", "ancient", "blind", "silence", "brain", "senses", "terrified"],
            "psychology": ["mind", "manipulation", "psychology", "brain", "trick", "dark", "secret", "people", "behavior", "lie", "sign", "control", "habit", "respect", "trap", "smile"],
            "animal_rescue": ["rescue", "puppy", "dog", "cat", "kitten", "saved", "heartbreaking", "miracle", "abandoned", "safe", "love", "trauma", "gentle", "healed"],
            "alternate_history": ["empire", "rome", "roman", "history", "what if", "timeline", "ancient", "war", "battle", "civilization", "century", "world", "army", "alexandria", "mongol"]
        }
        target_keywords = niche_kw_map.get(channel_niche, ["video", "fact", "world", "secret"])
        matched_kw = [kw for kw in target_keywords if kw in full_text.lower() or kw in title.lower()]
        if len(matched_kw) >= 3:
            niche_score = 9.5
            strengths.append(f"Đúng ngách {channel_niche} xuất sắc: khớp {len(matched_kw)} từ khóa chủ đề.")
        elif len(matched_kw) >= 1:
            niche_score = 8.5
            strengths.append(f"Khớp từ khóa ngách: {', '.join(matched_kw)}")
        else:
            niche_score = 7.0
            issues.append(f"Thiếu từ khóa đặc trưng của ngách {channel_niche}.")

        # Kiểm tra từ khóa lệch ngách nghiêm trọng (Bayern/bóng đá trên kênh động vật, tiêm chích trên kênh lịch sử)
        combined_lower = f"{title} {full_text}".lower()
        if channel_niche == "wildlife_predators" and any(bad in combined_lower for bad in ("bayern", "football", "soccer", "stadium", "fifa")):
            niche_score = max(3.0, niche_score - 5.0)
            issues.append("Vi phạm nghiêm trọng: Rò rỉ chủ đề bóng đá/thể thao vào kênh động vật hoang dã.")
        if channel_niche == "alternate_history":
            if any(bad in combined_lower for bad in ("inject", "vaccine", "syringe", "burn the evidence", "serial killer", "ted bundy", "btk killer", "gaslighting", "narcissist", "bloodbath", "execution order")):
                niche_score = max(3.0, niche_score - 5.0)
                issues.append("Vi phạm nghiêm trọng: Rò rỉ chủ đề nhạy cảm/lệch ngách (y tế, tội phạm, tâm lý học hoặc từ khóa bạo lực bị bóp phân phối) vào kênh Lịch sử Giả định.")
            if not any(cf in combined_lower for cf in ("what if", "if ", "would ", "never ", "almost ", "timeline", "alternate")):
                hook_score = max(4.5, hook_score - 2.5)
                issues.append("Thiếu cấu trúc giả thuyết 'What If / Counterfactual' đặc trưng của ngách Lịch sử Giả định (dễ biến thành bài giảng sử khô khan).")
        if channel_niche == "animal_rescue":
            title_l = title.lower()
            if any(b in title_l for b in ("frozen", "shivering", "starving", "tied in rain")) and not any(
                t in title_l for t in ("until", "finally", "moment", "realized", "refused", "saved", "safe", "miracle", "heart")
            ):
                hook_score = max(4.0, hook_score - 3.0)
                issues.append("Tiêu đề cứu hộ quá bi lụy, thiếu mồi câu chuyển hóa hy vọng (Until/Moment/Safe).")

        # 3. Kiểm tra Ngôn ngữ
        if is_pt:
            english_leaks = ["the", "and", "with", "this", "what", "watch", "subscribe", "today", "nature", "predators", "channel", "world"]
            leaks_found = [w for w in english_leaks if re.search(rf"\b{w}\b", full_text.lower())]
            if leaks_found:
                lang_score -= min(4.0, len(leaks_found) * 1.5)
                issues.append(f"Phát hiện từ tiếng Anh rò rỉ: {leaks_found}")
            else:
                lang_score = 9.5
                strengths.append("100% tiếng Bồ Đào Nha tự nhiên, không rò rỉ tiếng Anh.")
        else:
            lang_score = 9.5
            strengths.append("Ngôn ngữ tự nhiên, mạch lạc, chuẩn văn phong YouTube Shorts.")

        # 4. Kiểm tra Retention, Mid-Video Re-Hook & Payoff Depth
        num_scenes = len(scenes)
        total_words = len(full_text.split())
        if 6 <= num_scenes <= 12 and 55 <= total_words <= 145:
            retention_score += 1.2
            strengths.append(f"Cấu trúc {num_scenes} cảnh ({total_words} từ) đạt chuẩn nhịp cắt nhanh có Payoff sâu.")
        elif num_scenes < 6 or total_words < 50:
            retention_score -= 2.0
            issues.append(f"Kịch bản quá ngắn ({num_scenes} cảnh, {total_words} từ), thiếu chiều sâu Payoff.")

        # Kiểm tra kết thúc Infinite Loop Bridge (Zero Spoken Subscribe CTA)
        if scenes:
            last_scene = scenes[-1].get("narration", "")
            if not any(cta_word in last_scene.lower() for cta_word in ["inscreva-se", "se inscreva", "subscribe", "đăng ký kênh"]):
                retention_score = min(10.0, retention_score + 0.8)
                strengths.append("Cảnh cuối tuân thủ chuẩn Zero-Outro Infinite Loop Bridge (giữ APV > 100%).")

        total_score = int((hook_score * 0.30 + niche_score * 0.25 + lang_score * 0.25 + retention_score * 0.20) * 10)
        passed = total_score >= 80

        return {
            "passed": passed,
            "score": total_score,
            "hook_score": round(hook_score, 1),
            "niche_score": round(niche_score, 1),
            "language_score": round(lang_score, 1),
            "retention_score": round(retention_score, 1),
            "verdict": "APROVADO (ĐẠT CHUẨN)" if passed else "REPROVADO (CẦN TỐI ƯU)",
            "feedback": "Kịch bản đạt chuẩn giữ chân người xem YouTube Shorts." if passed else "Kịch bản cần cải thiện một số điểm.",
            "strengths": strengths,
            "weaknesses": issues,
        }

    # ═════════════════════════════════════════════════════════════════════════════
    # 2. ĐÁNH GIÁ & CHẤM ĐIỂM VIDEO THÀNH PHẨM (CALIBRATED REAL METRICS GATE)
    # ═════════════════════════════════════════════════════════════════════════════

    def evaluate_video(
        self,
        video_path: str,
        expected_duration_ms: int = 0
    ) -> Dict[str, Any]:
        """
        Chấm điểm chất lượng video thành phẩm bằng thang đo thực tế liên tục (không còn trả về 100 cứng):
        1. Stream & Portrait Integrity (0 - 25 điểm): 1080x1920 9:16, H.264 + AAC, ~30fps
        2. Bitrate & Visual Encoding Density (0 - 25 điểm): đo Mbps thực tế
        3. A/V Sync Precision (0 - 25 điểm): đo độ lệch mili-giây giữa video stream và audio stream
        4. Shorts Retention Duration Curve (0 - 15 điểm): so với khung vàng 30s - 45s
        5. Frame Sharpness & Contrast Inspection (0 - 10 điểm): trích xuất khung hình kiểm tra độ nét & màn đen
        Đạt chuẩn (passed=True): Tổng điểm >= 78/100 và không vi phạm lỗi chặn cứng.
        """
        import subprocess
        import tempfile
        from app.graph.evaluators.visual_critic import VisualCritic

        p = Path(video_path)
        if not p.exists() or p.stat().st_size < 500 * 1024:
            return {
                "passed": False,
                "score": 0,
                "issues": ["File video không tồn tại hoặc dung lượng rỗng (< 500KB)"],
                "feedback": "Render thất bại, chặn xuất bản."
            }

        file_size_mb = p.stat().st_size / (1024 * 1024)
        issues: List[str] = []
        strengths: List[str] = []
        hard_fail = False

        try:
            info = probe_file(video_path) or {}
            format_info = info.get("format", {})
            duration_sec = float(format_info.get("duration", 0.0))

            has_video = False
            has_audio = False
            width = 0
            height = 0
            fps = 0.0
            video_duration = 0.0
            audio_duration = 0.0
            audio_codec = ""
            video_codec = ""
            pix_fmt = ""
            color_range = ""
            audio_channels = 0
            audio_sample_rate = 0

            for stream in info.get("streams", []):
                st = stream.get("codec_type")
                if st == "video":
                    has_video = True
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                    video_codec = stream.get("codec_name", "").lower()
                    r_frame = stream.get("r_frame_rate", "30/1")
                    if "/" in r_frame:
                        num, den = r_frame.split("/")
                        fps = round(float(num) / float(den), 1) if float(den) > 0 else 30.0
                    video_duration = float(stream.get("duration", duration_sec) or duration_sec)
                    pix_fmt = str(stream.get("pix_fmt", "") or "").lower()
                    color_range = str(stream.get("color_range", "") or "").lower()
                elif st == "audio":
                    has_audio = True
                    audio_duration = float(stream.get("duration", duration_sec) or duration_sec)
                    audio_codec = stream.get("codec_name", "").lower()
                    audio_channels = int(stream.get("channels", 0) or 0)
                    audio_sample_rate = int(stream.get("sample_rate", 0) or 0)

            if not has_video or not has_audio:
                return {
                    "passed": False,
                    "score": 0,
                    "issues": ["Thiếu stream video hoặc stream âm thanh giọng đọc!"],
                    "feedback": "File MP4 thiếu luồng nghe/nhìn, chặn xuất bản."
                }

            # 1. Stream & Portrait Integrity (Tối đa 25 điểm)
            stream_pts = 0.0
            if width == 1080 and height == 1920:
                stream_pts += 15.0
                strengths.append(f"Chuẩn dọc Shorts 9:16 ({width}x{height}).")
            elif height > width and width >= 720:
                stream_pts += 11.0
                issues.append(f"Độ phân giải dọc chưa chuẩn 1080x1920 ({width}x{height}).")
            else:
                hard_fail = True
                issues.append(f"Sai tỉ lệ khung hình Shorts ({width}x{height}).")

            if 28.0 <= fps <= 61.0:
                stream_pts += 5.0
            else:
                stream_pts += 2.0
                issues.append(f"FPS lệch chuẩn ({fps} fps).")

            if audio_codec in ("aac", "mp3", "opus"):
                stream_pts += 5.0
            else:
                stream_pts += 2.0
                issues.append(f"Codec âm thanh '{audio_codec}' không tối ưu cho mobile.")

            if pix_fmt and pix_fmt != "yuv420p":
                hard_fail = True
                issues.append(f"Sai dải màu video (pix_fmt={pix_fmt}, yêu cầu yuv420p TV range) — chặn xuất bản!")
            elif color_range == "pc":
                hard_fail = True
                issues.append("Video dùng dải màu full-range (pc/yuvj420p) dễ lệch màu trên YouTube — chặn xuất bản!")
            else:
                strengths.append("Dải màu chuẩn yuv420p (TV range bt709).")

            if audio_channels != 2 or audio_sample_rate != 48000:
                hard_fail = True
                issues.append(f"Âm thanh chưa chuẩn Stereo 48kHz ({audio_channels}ch, {audio_sample_rate}Hz) — chặn xuất bản!")
            else:
                strengths.append("Âm thanh chuẩn Stereo 48 kHz.")

            # 2. Bitrate & Visual Encoding Density (Tối đa 25 điểm)
            mbps = (file_size_mb * 8.0) / max(duration_sec, 1.0)
            if 4.0 <= mbps <= 12.0:
                # Điểm liên tục từ 21 -> 25 tùy độ nét bitrate
                bitrate_pts = min(25.0, 21.0 + (mbps - 4.0) * 0.6)
                strengths.append(f"Bitrate sắc nét ({mbps:.2f} Mbps, {file_size_mb:.2f} MB).")
            elif 2.0 <= mbps < 4.0:
                bitrate_pts = 15.0 + (mbps - 2.0) * 3.0
            elif mbps > 12.0:
                bitrate_pts = 23.0
            else:
                bitrate_pts = max(5.0, mbps * 7.0)
                issues.append(f"Bitrate video thấp ({mbps:.2f} Mbps), dễ bị vỡ nét trên YouTube Shorts.")
                if mbps < 0.8:
                    hard_fail = True

            # 3. A/V Sync Precision (Tối đa 25 điểm)
            av_drift_ms = abs(video_duration - audio_duration) * 1000.0 if (video_duration > 0 and audio_duration > 0) else 0.0
            if expected_duration_ms > 0:
                exp_drift_ms = abs(duration_sec * 1000.0 - expected_duration_ms)
                # Chỉ ghi nhận lệch expected_duration_ms nếu chênh lệch lớn (> 650ms, dấu hiệu rớt 1 phân cảnh audio),
                # còn độ lệch thực tế giữa hình và tiếng trong file MP4 thành phẩm là abs(video_duration - audio_duration)
                if exp_drift_ms > 650.0:
                    av_drift_ms = max(av_drift_ms, min(exp_drift_ms, 800.0))

            if av_drift_ms <= 50.0:
                sync_pts = 25.0 - (av_drift_ms / 50.0) * 1.5
                strengths.append(f"Đồng bộ A/V chính xác (drift {av_drift_ms:.0f}ms).")
            elif av_drift_ms <= 250.0:
                sync_pts = 23.5 - ((av_drift_ms - 50.0) / 200.0) * 5.5
            elif av_drift_ms <= 550.0:
                sync_pts = 18.0 - ((av_drift_ms - 250.0) / 300.0) * 8.0
                issues.append(f"A/V Sync lệch {av_drift_ms:.0f}ms.")
            else:
                sync_pts = 5.0
                hard_fail = True
                issues.append(f"AV Desync nghiêm trọng ({av_drift_ms:.0f}ms > 550ms) — chặn xuất bản!")

            # 4. Shorts Retention Duration Curve (Tối đa 15 điểm — Khung vàng High-APV 27s - 42s)
            if 27.0 <= duration_sec <= 42.0:
                # Khung vàng giữ chân & APV > 80% cao nhất (đỉnh tại ~33.0s)
                dist_from_33 = abs(duration_sec - 33.0)
                dur_pts = max(13.5, 15.0 - dist_from_33 * 0.18)
                strengths.append(f"Thời lượng nằm trong khung vàng High-APV Retention ({duration_sec:.1f}s).")
            elif 20.0 <= duration_sec < 27.0:
                dur_pts = 10.0 + (duration_sec - 20.0) * 0.45
            elif 42.0 < duration_sec <= 60.0:
                dur_pts = 13.0 - (duration_sec - 42.0) * 0.25
            else:
                dur_pts = 4.0
                hard_fail = True
                issues.append(f"Thời lượng ngoài ngưỡng Shorts ({duration_sec:.1f}s, yêu cầu 20s-60s).")

            # 5. Frame Sharpness & Black-Screen Inspection tại Hook (Tối đa 10 điểm)
            frame_pts = 7.5
            sample_sharpness = 0.0
            try:
                with tempfile.TemporaryDirectory() as td:
                    f_hook = str(Path(td) / "hook_frame.jpg")
                    t_pos = min(1.0, max(0.2, duration_sec * 0.1))
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t_pos:.2f}", "-i", str(p), "-frames:v", "1", f_hook],
                        capture_output=True,
                        timeout=10,
                    )
                    if Path(f_hook).exists() and Path(f_hook).stat().st_size > 1000:
                        if VisualCritic._is_solid_color_or_black(f_hook):
                            frame_pts = 0.0
                            hard_fail = True
                            issues.append("Khung hình mở đầu (Hook) bị đen kịt hoặc đơn sắc lỗi!")
                        else:
                            sample_sharpness = VisualCritic._measure_sharpness(f_hook)
                            if sample_sharpness >= 6.0:
                                frame_pts = min(10.0, 7.0 + (sample_sharpness - 6.0) * 0.35)
                            elif sample_sharpness >= 4.2:
                                frame_pts = 5.0 + (sample_sharpness - 4.2) * 1.1
                                issues.append(f"Khung hình mở đầu hơi mờ (sharpness={sample_sharpness:.1f}).")
                            else:
                                frame_pts = 2.0
                                hard_fail = True
                                issues.append(f"Khung hình mở đầu quá mờ nhòe (sharpness={sample_sharpness:.1f} < 4.2)!")
            except Exception as ex_frame:
                logger.debug(f"[QualityEvaluator] Không thể trích xuất frame kiểm tra độ nét: {ex_frame}")

            # 6. Kiểm tra tự động Khung Đen (blackdetect), Đứng Hình (freezedetect) & Cắt Cảnh 3s Đầu (scdet)
            black_max_sec = 0.0
            freeze_max_sec = 0.0
            cuts_first_3s = 0
            total_cuts = 0
            try:
                import re as _re_qa
                vf_qa = "crop=800:760:140:280,blackdetect=d=0.10:pic_th=0.98,freezedetect=n=-45dB:d=2.2,scdet=t=5.0"
                res_qa = subprocess.run(
                    ["ffmpeg", "-v", "info", "-i", str(p), "-vf", vf_qa, "-an", "-f", "null", "-"],
                    capture_output=True,
                    text=True,
                    timeout=35,
                )
                qa_log = (res_qa.stderr or "") + "\n" + (res_qa.stdout or "")
                b_durs = [float(x) for x in _re_qa.findall(r"black_duration:\s*([0-9.]+)", qa_log)]
                if b_durs:
                    black_max_sec = max(b_durs)
                if black_max_sec >= 0.15:
                    hard_fail = True
                    frame_pts = 0.0
                    issues.append(f"Phát hiện khung hình đen kéo dài {black_max_sec:.2f}s (>= 0.15s) — chặn xuất bản!")

                f_durs = [float(x) for x in _re_qa.findall(r"freeze_duration:\s*([0-9.]+)", qa_log)]
                if f_durs:
                    freeze_max_sec = max(f_durs)
                if freeze_max_sec >= 2.3:
                    hard_fail = True
                    frame_pts = min(frame_pts, 2.0)
                    issues.append(f"Phát hiện ảnh/hình đứng yên {freeze_max_sec:.2f}s (>= 2.3s) — chặn xuất bản!")

                scd_times = sorted(float(x) for x in _re_qa.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", qa_log))
                total_cuts = len(scd_times)
                cuts_first_3s = sum(1 for t in scd_times if 0.15 <= t <= 5.20)
                if duration_sec >= 6.0 and cuts_first_3s == 0 and total_cuts < 3 and freeze_max_sec >= 1.8:
                    hard_fail = True
                    issues.append("Không có nhát cắt chuyển cảnh hoặc chuyển động thực trong 5 giây đầu — chặn xuất bản!")
                else:
                    strengths.append(f"Nhịp cắt/chuyển động mở đầu đạt chuẩn ({cuts_first_3s} cuts đầu, tổng {total_cuts} cuts).")

                if duration_sec >= 15.0:
                    timeline_pts = [0.0] + scd_times + [duration_sec]
                    max_gap = max(timeline_pts[i + 1] - timeline_pts[i] for i in range(len(timeline_pts) - 1))
                    if max_gap > 10.5:
                        hard_fail = True
                        issues.append(f"Khoảng cách không cắt cảnh quá dài ({max_gap:.1f}s > 10.5s) — chặn xuất bản!")
            except Exception as ex_qa_scan:
                logger.warning(f"[QualityEvaluator] Lỗi quét blackdetect/freezedetect/scdet: {ex_qa_scan}")

            total_raw = stream_pts + bitrate_pts + sync_pts + dur_pts + frame_pts
            final_score = int(round(max(0.0, min(99.0, total_raw))))
            passed = (final_score >= 78) and (not hard_fail)

            return {
                "passed": passed,
                "score": final_score,
                "duration_seconds": round(duration_sec, 2),
                "video_duration": round(video_duration, 2),
                "audio_duration": round(audio_duration, 2),
                "av_drift_ms": round(av_drift_ms, 1),
                "bitrate_mbps": round(mbps, 2),
                "hook_frame_sharpness": round(sample_sharpness, 2),
                "width": width,
                "height": height,
                "fps": fps,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "pix_fmt": pix_fmt,
                "color_range": color_range,
                "audio_channels": audio_channels,
                "audio_sample_rate": audio_sample_rate,
                "black_max_sec": round(black_max_sec, 3),
                "freeze_max_sec": round(freeze_max_sec, 3),
                "cuts_first_3s": cuts_first_3s,
                "total_cuts": total_cuts,
                "file_size_mb": round(file_size_mb, 2),
                "has_audio": has_audio,
                "has_video": has_video,
                "issues": issues,
                "strengths": strengths,
                "feedback": (
                    f"Video đạt chuẩn ({final_score}/100, {mbps:.1f}Mbps, drift {av_drift_ms:.0f}ms)."
                    if passed else f"Video bị chặn ({final_score}/100): {'; '.join(issues)}"
                ),
            }

        except Exception as e:
            logger.error(f"[QualityEvaluator] Lỗi kiểm tra video bằng FFprobe: {e}")
            return {
                "passed": False,
                "score": 0,
                "issues": [f"Lỗi đọc metadata: {e}"],
                "feedback": "Không thể kiểm tra file video — chặn xuất bản."
            }
