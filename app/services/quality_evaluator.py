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
        language: str = "pt-BR"
    ) -> Dict[str, Any]:
        """
        Đánh giá kịch bản theo 4 tiêu chí cốt lõi:
        1. Hook Strength (0-3s thu hút người xem ngay lập tức) - 30%
        2. Channel Niche Alignment (đúng ngách & phong cách kênh) - 25%
        3. Language Authenticity (chuẩn ngôn ngữ bản địa, không lai tạp) - 25%
        4. Retention Arc & Pacing (nhịp độ 4-beat, cao trào, CTA) - 20%
        Ngưỡng đạt chuẩn: Tổng điểm >= 80/100.
        """
        title = script_dict.get("title", "")
        scenes = script_dict.get("scenes", [])
        full_text = " ".join([s.get("narration", "") for s in scenes])

        # Cố gắng chấm điểm bằng Gemini AI
        ai_report = self._evaluate_script_with_ai(
            title=title,
            scenes=scenes,
            full_text=full_text,
            topic=topic,
            channel_niche=channel_niche,
            language=language
        )
        if ai_report:
            return ai_report

        # Fallback: Chấm điểm dựa trên hệ thống luật chuyên sâu (Rule-Based Heuristic)
        return self._heuristic_evaluate_script(
            title=title,
            scenes=scenes,
            full_text=full_text,
            topic=topic,
            channel_niche=channel_niche,
            language=language
        )

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
1. hook_score: O gancho dos primeiros 3 segundos (Cena 1) é eletrizante, desperta curiosidade irresistível e impede o swipe?
2. niche_score: O conteúdo está 100% alinhado com o nicho ({channel_niche})?
3. language_score: O português é 100% brasileiro, coloquial, natural, sem erros e SEM nenhuma palavra em inglês?
4. retention_score: Ritmo rápido, tensão crescente, clímax dramático e chamada para ação (CTA) envolvente?

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
1. hook_score: Does Scene 1 have an electrifying 3-second hook that creates irresistible curiosity and stops the swipe?
2. niche_score: Is the content 100% aligned with the niche ({channel_niche})?
3. language_score: Is the language natural, engaging, native-level phrasing without awkward artifacts?
4. retention_score: Fast pacing, escalating tension, dramatic climax, and compelling Call to Action (CTA)?

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
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
        }
        for model, k_use in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=6):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k_use}"
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
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
                elif resp.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(k_use, model, status_code=resp.status_code, error_text=resp.text[:250])
                    continue
            except Exception as e:
                logger.warning(f"[QualityEvaluator] Lỗi gọi Gemini ({model}) chấm điểm kịch bản: {e}")
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
        hook_score = 8.0
        niche_score = 8.5
        lang_score = 9.0
        retention_score = 8.0
        issues = []
        strengths = []

        # 1. Kiểm tra Cảnh 1 (Hook 0-3s)
        if scenes:
            scene_1 = scenes[0].get("narration", "")
            words_s1 = scene_1.split()
            if len(words_s1) < 4:
                hook_score -= 2.0
                issues.append("Cảnh 1 quá ngắn, chưa đủ thông tin làm mồi câu (Hook).")
            elif len(words_s1) > 22:
                hook_score -= 1.5
                issues.append("Cảnh 1 quá dài, nhịp điệu mở đầu bị chậm.")
            else:
                strengths.append(f"Cảnh 1 có độ dài lý tưởng ({len(words_s1)} từ) cho Hook 3s.")

            # Kiểm tra từ cảm thán hoặc câu hỏi giật gân
            hook_cues = ["?", "!", "Você", "Esse", "Essa", "O único", "Nunca", "Inacreditável", "Sabia", "Never", "If you", "This is why", "Did you know", "Watch"]
            if any(p in scene_1 for p in hook_cues):
                hook_score = min(10.0, hook_score + 1.5)
                strengths.append("Cảnh 1 có yếu tố kích thích trí tò mò hoặc nghịch lý.")

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
        if channel_niche == "alternate_history" and any(bad in combined_lower for bad in ("inject", "vaccine", "syringe", "burn the evidence")):
            niche_score = max(3.0, niche_score - 5.0)
            issues.append("Vi phạm nghiêm trọng: Rò rỉ chủ đề y tế/tiêm chích lệch khỏi lịch sử giả định.")
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
    # 2. ĐÁNH GIÁ & CHẤM ĐIỂM VIDEO THÀNH PHẨM (VIDEO QUALITY GATE)
    # ═════════════════════════════════════════════════════════════════════════════

    def evaluate_video(
        self,
        video_path: str,
        expected_duration_ms: int = 0
    ) -> Dict[str, Any]:
        """
        Kiểm định chất lượng kỹ thuật & tính thẩm mỹ video:
        - Tồn tại file & kích thước hợp lý (> 1MB)
        - Luồng video (h264, 1080x1920 portrait 9:16)
        - Luồng audio (aac, rõ ràng, không câm)
        - Thời lượng Shorts chuẩn (25s - 60s)
        - Điểm chất lượng: 0 - 100. Đạt chuẩn: >= 85/100.
        """
        p = Path(video_path)
        if not p.exists() or p.stat().st_size < 500 * 1024:
            return {
                "passed": False,
                "score": 0,
                "issues": ["File video không tồn tại hoặc dung lượng rỗng (< 500KB)"],
                "feedback": "Render thất bại, cần render lại."
            }

        file_size_mb = p.stat().st_size / (1024 * 1024)
        issues = []
        strengths = []
        score = 100

        try:
            info = probe_file(video_path) or {}
            format_info = info.get("format", {})
            duration_sec = float(format_info.get("duration", 0.0))

            has_video = False
            has_audio = False
            width = 1080
            height = 1920
            fps = 30.0
            video_duration = 0.0
            audio_duration = 0.0
            audio_codec = ""

            for stream in info.get("streams", []):
                st = stream.get("codec_type")
                if st == "video":
                    has_video = True
                    width = int(stream.get("width", 1080))
                    height = int(stream.get("height", 1920))
                    r_frame = stream.get("r_frame_rate", "30/1")
                    if "/" in r_frame:
                        num, den = r_frame.split("/")
                        fps = round(float(num) / float(den), 1) if float(den) > 0 else 30.0
                    video_duration = float(stream.get("duration", duration_sec) or duration_sec)
                elif st == "audio":
                    has_audio = True
                    audio_duration = float(stream.get("duration", duration_sec) or duration_sec)
                    audio_codec = stream.get("codec_name", "").lower()

            # Kiểm tra luồng
            if not has_video:
                issues.append("Thiếu stream video!")
                score -= 50
            if not has_audio:
                issues.append("Thiếu stream âm thanh giọng đọc!")
                score -= 50

            # [FIX Phase 3] Kiểm tra AV Sync: chênh lệch thời lượng video vs audio
            if has_video and has_audio and video_duration > 0 and audio_duration > 0:
                av_drift_ms = abs(video_duration - audio_duration) * 1000
                if av_drift_ms > 500:
                    issues.append(
                        f"⚠️ AV Desync nghiêm trọng: Video={video_duration:.2f}s vs Audio={audio_duration:.2f}s "
                        f"(lệch {av_drift_ms:.0f}ms > 500ms). Video sẽ bị desync khi phát."
                    )
                    score -= 20
                elif av_drift_ms > 200:
                    issues.append(
                        f"AV Sync hơi lệch: {av_drift_ms:.0f}ms (chấp nhận được nhưng cần chú ý)."
                    )
                    score -= 5
                else:
                    strengths.append(f"AV Sync xuất sắc (drift chỉ {av_drift_ms:.0f}ms).")

            # [FIX Phase 3] Kiểm tra codec âm thanh (AAC là chuẩn mobile-safe)
            if has_audio and audio_codec and audio_codec not in ("aac", "mp3", "opus"):
                issues.append(f"Codec âm thanh '{audio_codec}' không phải AAC — có thể không phát được trên iOS.")
                score -= 5

            # Kiểm tra tỉ lệ khung hình (Khuyến khích 9:16)
            if height > width:
                strengths.append(f"Độ phân giải dọc chuẩn Shorts 9:16 ({width}x{height}, {fps} FPS).")
            else:
                issues.append(f"Video không phải tỉ lệ dọc ({width}x{height}).")
                score -= 15

            # Kiểm tra thời lượng
            if 20.0 <= duration_sec <= 65.0:
                strengths.append(f"Thời lượng lý tưởng cho Shorts: {duration_sec:.1f}s.")
            elif duration_sec < 20.0:
                issues.append(f"Thời lượng quá ngắn ({duration_sec:.1f}s < 20s).")
                score -= 15
            else:
                issues.append(f"Thời lượng vượt quá chuẩn Shorts ({duration_sec:.1f}s > 60s).")
                score -= 10

            # Kiểm tra kích thước file
            if file_size_mb >= 2.0:
                strengths.append(f"Dung lượng video sắc nét ({file_size_mb:.2f} MB).")
            else:
                issues.append(f"Dung lượng hơi nhỏ ({file_size_mb:.2f} MB), có thể bitrate thấp.")
                score -= 5

            passed = (score >= 85) and has_video and has_audio
            return {
                "passed": passed,
                "score": max(0, score),
                "duration_seconds": round(duration_sec, 2),
                "video_duration": round(video_duration, 2),
                "audio_duration": round(audio_duration, 2),
                "av_drift_ms": round(abs(video_duration - audio_duration) * 1000, 1) if has_video and has_audio else None,
                "width": width,
                "height": height,
                "fps": fps,
                "audio_codec": audio_codec,
                "file_size_mb": round(file_size_mb, 2),
                "has_audio": has_audio,
                "has_video": has_video,
                "issues": issues,
                "strengths": strengths,
                "feedback": "Video hoàn hảo, sẵn sàng đăng tải!" if passed else "Video chưa đạt chuẩn chất lượng."
            }

        except Exception as e:
            logger.error(f"[QualityEvaluator] Lỗi kiểm tra video bằng FFprobe: {e}")
            return {
                "passed": False,
                "score": 40,
                "issues": [f"Lỗi đọc metadata: {e}"],
                "feedback": "Không thể kiểm tra file video."
            }
