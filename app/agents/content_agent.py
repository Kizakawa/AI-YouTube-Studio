import json
import logging
import requests
import time
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)


def build_language_integrity_instruction(language: str) -> str:
    lang_clean = (language or "en").strip().lower()
    is_vi = lang_clean.startswith("vi")
    is_pt = lang_clean.startswith("pt")
    if is_vi:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% TIẾNG VIỆT (VIETNAMESE)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: VIETNAMESE ('vi').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, dramatic, conversational VIETNAMESE.
  * Even if the topic, director notes, or instructions are in English, you MUST TRANSLATE and write all narration in VIETNAMESE.
  * STRICTLY FORBIDDEN: Do NOT output English spoken narration.
- TITLE REQUIREMENT:
  * 'title' MUST be in VIETNAMESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- VN', '- VI', '(Việt Nam)'.
- NEVER mix languages (e.g. Vietnamese narration with English title or vice-versa).
═══════════════════════════════════════════════════════════════════
"""
    elif is_pt:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% PORTUGUÊS BRASILEIRO (pt-BR)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: BRAZILIAN PORTUGUESE ('pt-BR').
- SPOKEN NARRATION & ON-SCREEN HUD REQUIREMENT:
  * 'hook', 'full_narration', 'narration', 'overlay_badge', 'thumbnail_text', 'description', and 'pinned_comment' in EVERY scene MUST be written 100% in natural, dramatic, colloquial BRAZILIAN PORTUGUESE (português do Brasil).
  * Even if the topic, director notes, or niche instructions are in English, you MUST TRANSLATE and write ALL narration and badges in BRAZILIAN PORTUGUESE.
  * Use vocabulary and expressions natural to Brazil: 'onça-pintada', 'sucuri', 'jacaré', 'incrível', 'absurdo', 'impressionante', 'jamais', NOT European Portuguese.
  * STRICTLY FORBIDDEN: Do NOT output English or European Portuguese spoken narration.
  * ZERO SPOKEN OUTRO CTA: NEVER say 'Inscreva-se', 'Curta', or 'Deixe nos comentários' inside any scene's spoken `narration`! Put engagement questions ONLY in `pinned_comment`, and end the final scene's `narration` with a seamless Infinite Loop Bridge back to Scene 1!
- VISUAL SEARCH & AI PROMPT REQUIREMENT (CRITICAL FOR PEXELS / PIXABAY / FLUX):
  * 'visual_keyword', 'visual_prompt', and 'stock_query' MUST ALWAYS BE IN CONCRETE ENGLISH (e.g., "jaguar hunting caiman river", "deep ocean sonar submarine", "giant river otter amazon") so stock video APIs (Pexels/Pixabay) and AI image generators return 100% accurate visuals!
- TITLE REQUIREMENT:
  * 'title' MUST be 100% in BRAZILIAN PORTUGUESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- PT', '- BR', '(Brasil)'.
- thumbnail_text MUST also be in PORTUGUÊS BRASILEIRO (3-5 words ALL CAPS + 1 emoji).
═══════════════════════════════════════════════════════════════════
"""
    else:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% ENGLISH (AMERICAN)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: ENGLISH ('en').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, punchy, conversational AMERICAN ENGLISH.
  * Even if the topic, director notes, or niche instructions are in Vietnamese, you MUST TRANSLATE and write all narration in ENGLISH.
  * STRICTLY FORBIDDEN: Do NOT output Vietnamese spoken narration.
- TITLE REQUIREMENT:
  * 'title' MUST be in ENGLISH (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- EN', '- US', '(English)'.
- NEVER mix languages (e.g. English narration with Vietnamese title or vice-versa).
═══════════════════════════════════════════════════════════════════
"""


class ContentAgent:
    """Agent chịu trách nhiệm lên ý tưởng, viết kịch bản giật gân (hook), phân cảnh và metadata SEO.
    Đã tích hợp KeyRotator để xoay vòng API Keys qua nhiều tài khoản Google Ultra bằng direct HTTP REST
    nhằm đảm bảo tốc độ cao, không chặn event loop và không bị kẹt retry gRPC.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self.session = requests.Session()

    def _call_api(self, prompt: str, json_mode: bool = False, timeout: int = 20, max_total_seconds: int = 60) -> str:
        """
        Gọi trực tiếp Gemini REST API theo chiến lược ưu tiên Model mạnh nhất trước:
        1. Thử các model Flagship (gemini-3.8-flash, gemini-3.5-flash, gemini-3.6-flash, gemini-3-flash-preview) qua toàn bộ pool API Keys.
        2. Khi một cặp (Key, Model) gặp 429, chỉ đánh dấu cooldown cho cặp đó để thử Key khác hoặc tự động hạ bậc xuống dòng Flash-Lite.
        """
        import time
        start_time = time.monotonic()
        total_keys = max(len(self.rotator.keys), 1)
        attempts = 0

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        for model, key in self.rotator.iter_best_model_key_pairs(AVAILABLE_MODELS):
            elapsed = time.monotonic() - start_time
            if elapsed >= max_total_seconds:
                raise RuntimeError(
                    f"Gemini API: Vượt tổng timeout {max_total_seconds}s "
                    f"(đã thử {attempts} requests trong {elapsed:.1f}s)"
                )

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "safetySettings": safety_settings,
            }
            if json_mode:
                payload["generationConfig"] = {"responseMimeType": "application/json"}

            try:
                attempts += 1
                r = self.session.post(url, json=payload, timeout=timeout)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
                        full_text = "".join(texts).strip()
                        if full_text:
                            logger.info(f"✅ Gemini API ({model}) thành công với key {key[:8]}... trong {time.monotonic()-start_time:.1f}s")
                            return full_text
                elif r.status_code in (429, 403, 404):
                    err_txt = r.text[:800]
                    logger.warning(f"⚠️ ({model}) trên key {key[:8]}... gặp mã {r.status_code}. Chuyển tiếp ứng viên (Key/Model) kế tiếp...")
                    self.rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=err_txt)
                    continue
                elif r.status_code == 503 and json_mode:
                    # Fallback: Thử lại không dùng responseMimeType (tránh 503 trên engine structured JSON)
                    logger.info(f"Gemini API ({model}) gặp 503 với structured output, thử lại chế độ raw JSON...")
                    time.sleep(0.4)
                    try:
                        fallback_payload = {
                            "contents": [{"parts": [{"text": prompt}]}],
                            "safetySettings": safety_settings,
                        }
                        r_fb = self.session.post(url, json=fallback_payload, timeout=timeout)
                        if r_fb.status_code == 200:
                            data = r_fb.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
                                full_text = "".join(texts).strip()
                                if full_text:
                                    logger.info(f"✅ Gemini API ({model}) thành công qua fallback raw JSON trong {time.monotonic()-start_time:.1f}s")
                                    return full_text
                    except Exception as e_fb:
                        logger.debug(f"Fallback raw JSON lỗi: {e_fb}")
                    self.rotator.mark_model_rate_limited(key, model, status_code=503)
                    continue
                elif r.status_code == 503:
                    logger.warning(f"⚠️ Model {model} đang quá tải tạm thời (503). Chuyển sang model tiếp theo...")
                    self.rotator.mark_model_rate_limited(key, model, status_code=503)
                    continue
                else:
                    err_msg = ""
                    try:
                        err_msg = r.json().get("error", {}).get("message", "")[:80]
                    except Exception:
                        err_msg = r.text[:80]
                    logger.warning(f"Gemini API ({model}) trả lỗi {r.status_code} với key {key[:8]}...: {err_msg}")
                    continue
            except Exception as e:
                logger.warning(f"Lỗi kết nối Gemini API ({model}) với key {key[:8]}...: {e}")
                self.rotator.mark_model_rate_limited(key, model, status_code=503, error_text=str(e))
                time.sleep(0.3)
                continue

        # Fallback đám mây độc lập (timeout ngắn 10s) khi toàn bộ pool Gemini API tạm chạm trần 429/503
        try:
            r_cloud = self.session.post(
                "https://text.pollinations.ai/openai",
                headers={"Content-Type": "application/json"},
                json={
                    "model": "openai",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an elite viral YouTube Shorts scriptwriter. Respond ONLY with complete valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.5,
                },
                timeout=10,
            )
            if r_cloud.status_code == 200:
                c_data = r_cloud.json()
                choices = c_data.get("choices") or []
                if choices:
                    c_txt = str(choices[0].get("message", {}).get("content") or "").strip()
                    if c_txt and c_txt.count("{") >= 1 and c_txt.endswith("}"):
                        logger.info(
                            f"✅ Cloud LLM Fallback (openai) thành công khi Gemini 429 trong {time.monotonic()-start_time:.1f}s"
                        )
                        return c_txt
        except Exception as ex_cloud:
            logger.debug(f"Cloud LLM Fallback warning: {ex_cloud}")

        raise RuntimeError(
            f"Đã thử tất cả {attempts} requests qua {total_keys} keys và "
            f"{len(AVAILABLE_MODELS)} models trong {time.monotonic() - start_time:.1f}s — không nhận được phản hồi."
        )

    def _parse_json_script(
        self,
        raw_text: str,
        channel_niche: str = "general",
        topic: str = "",
    ) -> ScriptContract:
        """Helper làm sạch markdown và parse JSON thành ScriptContract, tự động khử rò rỉ ngôn ngữ và metadata chéo ngách."""
        import re
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
        raw_data = json.loads(text)

        if topic and ("topic" not in raw_data or not str(raw_data.get("topic") or "").strip()):
            raw_data["topic"] = str(topic).strip()

        # Khử sạch các hậu tố kênh hoặc mã quốc gia rò rỉ ở tiêu đề (tránh thuật toán YouTube phân phối nhầm vùng)
        if "title" in raw_data and isinstance(raw_data["title"], str):
            cleaned_title = re.sub(
                r'[\[\(\s\-_|:]+[\w\s]*?\b(?:VN|VI|Vietnamese|Vietnam)\b.*$',
                '',
                raw_data["title"],
                flags=re.IGNORECASE
            ).strip()
            if cleaned_title:
                raw_data["title"] = cleaned_title

        # Tự động bù đắp các trường bắt buộc của ScriptContract nếu LLM vô tình bỏ sót
        scenes = raw_data.get("scenes", [])

        # ── 1. ĐẢM BẢO TÍNH TOÀN VẸN CỦA PHÂN CẢNH (NON-DESTRUCTIVE CONTRACT VALIDATOR) ──
        # Tôn trọng 100% quyền tự quyết của Agent (LLM) dựa trên nội dung kịch bản và ngữ cảnh từng cảnh.
        # TUYỆT ĐỐI KHÔNG dùng regex từ khóa (động vật, thú, rừng...) để ghi đè hay thay thế các trường
        # visual_keyword, visual_prompt, stock_query thành các mẫu có sẵn (tránh cảnh tiêm thuốc biến thành thú rừng).
        banned_abstract = ["psychology", "minimalism", "authority", "power", "wealth", "mindset", "discipline", "history", "secret", "truth"]
        camera_jargon_re = re.compile(
            r"\b(8k|4k|uhd|hd|9:16|16:9|vertical\s+shot|macro\s+shot|wide[- ]angle\s+shot|wide[- ]angle|"
            r"extreme\s+close[- ]up|camera\s+pans?|dolly\s+zoom|photorealistic|hyper[- ]realistic|"
            r"unreal\s+engine|octane\s+render|35mm\s+film|aspect\s+ratio)\b",
            re.IGNORECASE,
        )
        seen_visual_keywords: set = set()

        for sc in scenes:
            if not isinstance(sc, dict):
                continue

            # Đảm bảo visual_keyword ngắn gọn (2-6 từ), sạch từ khóa kỹ thuật máy quay để tối ưu cho Top Search Google Images
            vk = str(sc.get("visual_keyword") or "").strip()
            vk = camera_jargon_re.sub(" ", vk)
            vk = re.sub(r"\s+", " ", vk).strip(" ,.-:")
            if not vk:
                sq_fallback = camera_jargon_re.sub(" ", str(sc.get("stock_query") or "").strip())
                sq_fallback = re.sub(r"\s+", " ", sq_fallback).strip(" ,.-:")
                if sq_fallback:
                    vk = sq_fallback
                else:
                    vp_clean = camera_jargon_re.sub(" ", str(sc.get("visual_prompt") or "").strip())
                    vp_words = [w.strip(",.:;!?") for w in vp_clean.split() if len(w.strip(",.:;!?")) > 2]
                    vk = " ".join(vp_words[:4]) if vp_words else "dramatic cinematic scene"
            if len(vk.split()) > 6:
                vk = " ".join(vk.split()[:6])

            # Đảm bảo visual_prompt không bị rỗng
            vp = str(sc.get("visual_prompt") or "").strip()
            if not vp:
                sc["visual_prompt"] = f"Cinematic 8k photorealistic vertical shot of {vk}"

            # Đảm bảo stock_query không bị rỗng và lọc nhẹ các từ trừu tượng cấm kỵ của Pexels
            sq = str(sc.get("stock_query") or "").strip()
            sq = camera_jargon_re.sub(" ", sq)
            if not sq:
                sq = " ".join(vk.split()[:4])
            else:
                for b in banned_abstract:
                    sq = re.sub(rf'\b{b}\b', '', sq, flags=re.IGNORECASE).strip()
                sq = re.sub(r'\s+', ' ', sq).strip(" ,.-:")
                if not sq:
                    sq = " ".join(vk.split()[:4])
            sc["stock_query"] = sq

            # Chống trùng lặp visual_keyword giữa các phân cảnh trong cùng 1 kịch bản
            vk_norm = vk.lower()
            if vk_norm in seen_visual_keywords:
                sq_norm = sq.lower()
                if sq and sq_norm not in seen_visual_keywords and sq_norm != vk_norm:
                    vk = sq
                else:
                    vp_clean = camera_jargon_re.sub(" ", str(sc.get("visual_prompt") or "")).strip()
                    extra_words = [
                        w.strip(",.:;!?").lower()
                        for w in vp_clean.split()
                        if len(w.strip(",.:;!?")) > 3 and w.strip(",.:;!?").lower() not in vk_norm.split()
                    ]
                    if extra_words:
                        vk = " ".join((vk.split()[:4] + extra_words[:2])[:6])
            seen_visual_keywords.add(vk.lower())
            sc["visual_keyword"] = vk

            # Đảm bảo media_type có giá trị mặc định nếu LLM chưa chỉ định
            if not sc.get("media_type"):
                sc["media_type"] = "auto"

        def _auto_extract_scene_highlights(narration_text: str) -> List[str]:
            """Tự động trích xuất 1-2 từ khóa đắt giá nhất (số liệu, từ viết hoa, động từ/danh từ mạnh) từ câu thoại của cảnh."""
            if not narration_text:
                return []
            _stop = {
                "the", "and", "that", "this", "with", "from", "your", "have", "they", "their",
                "what", "when", "where", "which", "while", "into", "about", "there", "these",
                "those", "would", "could", "should", "because", "until", "after", "before",
                "under", "over", "between", "through", "during", "without", "within", "along",
                "những", "trong", "không", "được", "chúng", "nhưng", "người", "khiến", "chính",
                "você", "para", "como", "mais", "pelo", "pela", "isso", "está", "quando",
            }
            tokens = narration_text.split()
            picked: List[str] = []
            # 1. Ưu tiên tuyệt đối các con số / thống kê / đơn vị đo lường
            for tok in tokens:
                cl = re.sub(r'[^\w\d%]', '', tok).upper()
                if cl and any(c.isdigit() for c in cl) and cl not in picked:
                    picked.append(cl)
                    if len(picked) >= 2:
                        return picked
            # 2. Ưu tiên các từ trong POWER_WORDS hoặc từ mang sắc thái mạnh >= 5 ký tự
            try:
                from app.utils.subtitle_helper import POWER_WORDS as _PW
            except Exception:
                _PW = set()
            for tok in tokens:
                cl = re.sub(r'[^\w\d]', '', tok).upper()
                if len(cl) >= 4 and cl in _PW and cl not in picked:
                    picked.append(cl)
                    if len(picked) >= 2:
                        return picked
            # 3. Nếu chưa có, chọn từ nội dung dài nhất (>= 5 ký tự, không thuộc stop_words)
            candidates = []
            for tok in tokens:
                cl = re.sub(r'[^\w\d]', '', tok)
                if len(cl) >= 5 and cl.lower() not in _stop:
                    candidates.append(cl.upper())
            candidates.sort(key=len, reverse=True)
            for cand in candidates:
                if cand not in picked:
                    picked.append(cand)
                    if len(picked) >= 2:
                        break
            return picked[:2]

        # ── 2. KHỬ BẪY OUTRO CTA ("SUBSCRIBE...") & KHÓA VÒNG LẶP VÔ TẬN (INFINITE LOOP BRIDGE) ──
        # Phân tích telemetry thực tế: Câu kêu gọi "Subscribe right now..." ở cảnh cuối làm tụt 10-15% APV
        # ở 5 giây cuối vì báo hiệu video đã hết. Khử tự động mọi câu CTA đọc thành tiếng ở 2 cảnh cuối!
        _cta_sentence_pattern = re.compile(
            r'\b(?:subscribe|inscreva-se|se\s+inscreva|đăng\s+ký\s+kênh|bấm\s+đăng\s+ký|nhấn\s+đăng\s+ký|'
            r'follow\s+for\s+more|leave\s+a\s+comment|leave\s+your\s+theory|drop\s+a\s+comment|'
            r'deixe\s+nos\s+comentários|deixe\s+sua\s+teoria|curta\s+e\s+siga|hit\s+the\s+bell|'
            r'để\s+lại\s+bình\s+luận|bấm\s+theo\s+dõi)\b',
            re.IGNORECASE
        )
        _cta_clause_tail_pattern = re.compile(
            r'[\,\;\-\—]+\s*(?:and\s+|so\s+|então\s+|e\s+|và\s+|hãy\s+)?'
            r'(?:subscribe|inscreva-se|se\s+inscreva|đăng\s+ký|bấm\s+đăng\s+ký|follow\s+for\s+more|'
            r'leave\s+a\s+comment|leave\s+your\s+theory|deixe\s+sua\s+teoria|deixe\s+nos\s+comentários).*$',
            re.IGNORECASE
        )

        if scenes and isinstance(scenes, list):
            for check_idx in range(max(0, len(scenes) - 2), len(scenes)):
                sc_obj = scenes[check_idx]
                if not isinstance(sc_obj, dict):
                    continue
                raw_narr = str(sc_obj.get("narration") or "").strip()
                if not raw_narr:
                    continue
                sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', raw_narr) if s.strip()]
                kept_sentences = []
                for sent in sentences:
                    if _cta_sentence_pattern.search(sent):
                        # Thử cắt vế đuôi nếu câu đầu có nội dung thực tế trước dấu phẩy
                        trimmed_sent = _cta_clause_tail_pattern.sub('.', sent).strip()
                        if not _cta_sentence_pattern.search(trimmed_sent) and len(trimmed_sent.split()) >= 5:
                            kept_sentences.append(trimmed_sent)
                    else:
                        kept_sentences.append(sent)
                sc_obj["narration"] = " ".join(kept_sentences).strip()

            # Nếu cảnh cuối cùng bị rỗng hoặc quá cụt (< 4 từ) sau khi lột bỏ câu Subscribe CTA:
            last_sc = scenes[-1] if scenes else None
            if isinstance(last_sc, dict):
                last_words = str(last_sc.get("narration") or "").strip().split()
                if len(last_words) < 4:
                    if len(scenes) >= 8:
                        # Đã đủ số cảnh (>= 7 cảnh sau khi bỏ), loại bỏ luôn cảnh rác chỉ chứa CTA
                        scenes.pop()
                    else:
                        # Tạo cầu nối Infinite Loop Bridge tự nhiên nối thẳng về Hook Cảnh 1
                        comb_text = f"{raw_data.get('title', '')} {raw_data.get('full_narration', '')}".lower()
                        is_pt_script = any(w in comb_text for w in ("você", "não", "então", "coração", "selva", "olhos", "jamais", "oceano", "onça", "sucuri", "jacaré", "ciência", "mistério"))
                        is_vn_script = bool(re.search(r'[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]', comb_text)) and not is_pt_script
                        if is_pt_script:
                            last_sc["narration"] = "E nesse instante o ciclo recomeça diante de você."
                        elif is_vn_script:
                            last_sc["narration"] = "Và khoảnh khắc đó mở ra sự thật từ đầu."
                        elif any(k in comb_text for k in ("puppy", "kitten", "rescue", "stray", "shelter", "abandoned", "dog", "cat")):
                            last_sc["narration"] = "And that moment of trust starts his new life."
                        else:
                            last_sc["narration"] = "And that hidden mechanism starts the cycle all over."

        # ── 2.4. KHỬ PHẾ PHẨM MỞ ĐẦU CẢNH 1 (ZERO-DELAY HOOK SANITIZER - FRAME 0.0s) ──
        # Tuyệt đối không cho phép câu mở đầu lãng phí 0.8s đầu tiên vào các cụm từ sáo rỗng ("Stop scrolling!", "Did you know...", "Bạn có biết...").
        if scenes and isinstance(scenes, list) and isinstance(scenes[0], dict):
            raw_h1 = str(scenes[0].get("narration") or "").strip()
            if raw_h1:
                _dead_hook_prefix = re.compile(
                    r'^(?:stop\s+scrolling(?:\s+right\s+now)?[\!\.\,\s\-—]*|'
                    r'wait\s+for\s+it[\!\.\,\s\-—]*|'
                    r'watch\s+till\s+the\s+end[\!\.\,\s\-—]*|'
                    r'did\s+you\s+know(?:\s+that)?[\?\!\.\,\s\-—]*|'
                    r'have\s+you\s+ever\s+wondered(?:\s+why|\s+how)?[\?\!\.\,\s\-—]*|'
                    r'here\s+is\s+(?:a\s+fun\s+fact|why|what\s+happens)[\:\!\.\,\s\-—]*|'
                    r'in\s+this\s+video[\,\s\-—]*|'
                    r'bạn\s+có\s+biết(?:\s+rằng|\s+là)?[\?\!\.\,\s\-—]*|'
                    r'đừng\s+lướt\s+qua(?:\s+nhé|\s+video\s+này)?[\!\.\,\s\-—]*|'
                    r'hôm\s+nay\s+chúng\s+ta\s+sẽ\s+tìm\s+hiểu[\:\!\.\,\s\-—]*|'
                    r'você\s+sabia(?:\s+que)?[\?\!\.\,\s\-—]*|'
                    r'pare\s+de\s+rolar(?:\s+agora)?[\!\.\,\s\-—]*|'
                    r'espere\s+até\s+o\s+final[\!\.\,\s\-—]*)+',
                    re.IGNORECASE
                )
                cleaned_h1 = _dead_hook_prefix.sub('', raw_h1).strip()
                if cleaned_h1 and len(cleaned_h1.split()) >= 3:
                    if cleaned_h1[0].islower():
                        cleaned_h1 = cleaned_h1[0].upper() + cleaned_h1[1:]
                    scenes[0]["narration"] = cleaned_h1

        # ── 2.5. TỰ ĐỘNG CHIA NHỊP CẮT CẢNH SIÊU TỐC (ULTRA-FAST SCENE PACING SPLITTER: 1.8s - 2.5s/CẢNH) ──
        # Đảm bảo Cảnh 1 Hook <= 7 từ (~1.5s - 2.0s) và các cảnh thân <= 10 từ (~1.8s - 2.5s), triệt tiêu cảm giác dài lê thê
        def _split_long_narration(text: str, prefer_word_idx: int) -> Optional[tuple]:
            text_clean = text.strip()
            words = text_clean.split()
            if len(words) < 8:
                return None

            # Ưu tiên 1: Tách theo ranh giới câu (. ! ?) nếu có >= 2 câu và mỗi vế >= 3 từ
            sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text_clean) if s.strip()]
            if len(sents) >= 2:
                acc = []
                for i_s, s_part in enumerate(sents[:-1]):
                    acc.append(s_part)
                    left_str = " ".join(acc).strip()
                    right_str = " ".join(sents[i_s + 1:]).strip()
                    if len(left_str.split()) >= 4 and len(right_str.split()) >= 4:
                        return left_str, right_str

            # Riêng với Cảnh 1 (prefer_word_idx == 6) nếu câu ghép quá dài (>= 12 từ) có dấu phẩy + liên từ độc lập
            # (", yet ", ", but ", ", mas ", ", e ") -> Tách gọn để Cảnh 1 luôn dứt điểm trong 5-8 từ (~2.0s)!
            if prefer_word_idx <= 6 and len(words) >= 12:
                import re as _re
                m_conj = _re.search(r'^([^,]{18,65}),\s+(?:yet|but|and|mas|porém|contudo)\s+(.{15,})$', text_clean, flags=_re.IGNORECASE)
                if m_conj:
                    l_part = m_conj.group(1).strip().rstrip(",.") + "!"
                    r_part = m_conj.group(2).strip()
                    if r_part and r_part[0].islower():
                        r_part = r_part[0].upper() + r_part[1:]
                    if 4 <= len(l_part.split()) <= 10 and len(r_part.split()) >= 4:
                        return l_part, r_part
            return None

        if scenes and isinstance(scenes, list):
            paced_scenes: List[Dict[str, Any]] = []
            for idx_sc, sc in enumerate(scenes):
                if not isinstance(sc, dict):
                    continue
                narr_str = str(sc.get("narration") or "").strip()
                w_list = narr_str.split()
                w_count = len(w_list)

                # Chỉ tách câu khi câu quá dài (>= 18 từ, ghép 2 mệnh đề) VÀ có dấu ngắt câu/liên từ tự nhiên
                should_split = (
                    (idx_sc == 0 and w_count >= 13)
                    or (idx_sc > 0 and w_count >= 18 and len(paced_scenes) + (len(scenes) - idx_sc) < 9)
                )
                target_pref = 6 if idx_sc == 0 else max(7, w_count // 2)
                split_res = _split_long_narration(narr_str, target_pref) if should_split else None

                if split_res:
                    left_narr, right_narr = split_res
                    sc_first = dict(sc)
                    sc_first["narration"] = left_narr
                    sc_first["estimated_seconds"] = round(max(2.2, len(left_narr.split()) / 2.8), 1)
                    sc_first["highlight_words"] = _auto_extract_scene_highlights(left_narr)

                    sc_second = dict(sc)
                    sc_second["narration"] = right_narr
                    sc_second["overlay_badge"] = None
                    sc_second["highlight_words"] = _auto_extract_scene_highlights(right_narr)
                    base_vk = str(sc.get("visual_keyword") or "cinematic scene").strip()
                    base_sq = str(sc.get("stock_query") or base_vk).strip()
                    base_vp = str(sc.get("visual_prompt") or f"Cinematic 8k vertical shot of {base_vk}").strip()
                    # Trích xuất danh từ dài từ câu thoại thứ 2 để làm mới truy vấn hình ảnh
                    right_nouns = [
                        re.sub(r'[^\w]', '', w).lower()
                        for w in right_narr.split()
                        if len(re.sub(r'[^\w]', '', w)) >= 5
                        and re.sub(r'[^\w]', '', w).lower() not in banned_abstract
                    ]
                    extra_focus = " ".join(right_nouns[:2]) if right_nouns else ""
                    sc_second["visual_keyword"] = f"{base_vk} {extra_focus}".strip()[:65] if extra_focus else base_vk
                    sc_second["stock_query"] = base_sq
                    sc_second["visual_prompt"] = base_vp
                    sc_second["estimated_seconds"] = round(max(2.2, len(right_narr.split()) / 2.8), 1)

                    paced_scenes.append(sc_first)
                    paced_scenes.append(sc_second)
                else:
                    sc["estimated_seconds"] = round(max(2.2, min(4.8, w_count / 2.8)), 1)
                    paced_scenes.append(sc)

            # Đánh lại số thứ tự scene_id chuẩn xác 1..N và làm giàu highlight_words / camera_motion / sfx_cue
            _motion_cycle = ["zoom_in_fast", "ken_burns_zoom_out", "pan_right", "ken_burns_zoom_in", "pan_left"]
            for new_id, p_sc in enumerate(paced_scenes, start=1):
                p_sc["scene_id"] = new_id
                if not p_sc.get("highlight_words"):
                    p_sc["highlight_words"] = _auto_extract_scene_highlights(str(p_sc.get("narration") or ""))
                if new_id == 1:
                    p_sc["camera_motion"] = "hyper_zoom"
                    p_sc["sfx_cue"] = "impact_boom"
                else:
                    if not p_sc.get("camera_motion"):
                        p_sc["camera_motion"] = _motion_cycle[(new_id - 1) % len(_motion_cycle)]
                    if not p_sc.get("sfx_cue"):
                        p_sc["sfx_cue"] = "riser_tension" if new_id == 4 else "whoosh_fast"
            scenes = paced_scenes
            raw_data["scenes"] = scenes

        # ── 2.7. SCENE 1 "WOW-FACTOR" OPENING VISUAL & THUMB-STOPPER HOOK AMPLIFIER (FRAME 0.0s) ──
        # Khung hình 0.0s quyết định 85% Tỉ lệ ở lại xem (Viewed vs. Swiped Away).
        # Tuyệt đối khử các từ khóa tĩnh/thụ động ở Cảnh 1 và bơm hiệu ứng thị giác choáng ngợp (Scale Shock / Mid-Action / Eye-Contact).
        if scenes and isinstance(scenes, list) and isinstance(scenes[0], dict):
            sc1 = scenes[0]
            comb_ctx = f"{raw_data.get('title', '')} {sc1.get('narration', '')} {sc1.get('visual_keyword', '')}".lower()
            _passive_hook_words = r'\b(?:standing|sitting|sleeping|resting|calm|peaceful|empty|still|waiting|walking\s+slowly|idle)\b'
            for q_field in ("stock_query", "visual_keyword"):
                val_q = str(sc1.get(q_field) or "").strip()
                if val_q:
                    cleaned_q = re.sub(_passive_hook_words, "", val_q, flags=re.IGNORECASE)
                    cleaned_q = re.sub(r'\s+', ' ', cleaned_q).strip()
                    if cleaned_q:
                        sc1[q_field] = cleaned_q

            vp1 = str(sc1.get("visual_prompt") or "").strip()
            # Đặt chủ thể cụ thể lên ĐẦU câu prompt (để nếu fallback sang tìm kiếm ảnh thì từ đầu luôn là danh từ thật,
            # tuyệt đối không chèn "JAW-DROPPING FRAME-0 HOOK" lên đầu làm hỏng truy vấn Google Images!)
            if "high-contrast" not in vp1.lower() and "rim lighting" not in vp1.lower():
                core_subj = (
                    str(raw_data.get("primary_entity") or sc1.get("stock_query") or sc1.get("visual_keyword") or "").strip()
                )
                lead_subj = f"{core_subj}: " if (core_subj and core_subj.lower() not in vp1.lower()) else ""
                sc1["visual_prompt"] = (
                    f"{lead_subj}{vp1}, peak dramatic action, razor-sharp high-contrast cinematic photography, "
                    f"intense focal depth, vivid rim lighting"
                ).strip(", ")

        # ── 2.8. ĐẢM BẢO MỌI PHÂN CẢNH SẠCH TAG ĐẠO DIỄN ([LOOP], [HOOK]) & KHÔNG DÍNH TỪ LỬNG ĐA NGÔN NGỮ ──
        if scenes and isinstance(scenes, list):
            _stage_tag_re = re.compile(
                r'\[(?:LOOP|HOOK|ACT\s*\d*|SCENE\s*\d*|PAUSE|BEAT|SILENCE|WHISPER|INTRO|OUTRO|END)[^\]]*\]',
                re.IGNORECASE,
            )
            _dangling_end_re = re.compile(
                r'[\s\.\,\-\—\…]+(?:because|which|and|so|that|but|with|or|while|as|if|since|although|though|where|'
                r'of|to|for|from|in|on|at|by|into|onto|until|his|her|their|its|my|your|our|these|this|those|the|a|an|is|are|was|were|why|how|what|when|'
                r'que|de|do|da|dos|das|pois|porque|para|com|em|no|na|nos|nas|mas|ou|quando|onde|'
                r'bởi vì|cho nên|nhưng|hoặc|của|với| tại|trong)[\s\.\,\-\—\…]*$',
                re.IGNORECASE,
            )
            for idx_s, sc_obj in enumerate(scenes):
                if not isinstance(sc_obj, dict):
                    continue
                s_narr = str(sc_obj.get("narration") or "").strip()
                s_narr = _stage_tag_re.sub("", s_narr)
                s_narr = re.sub(r'\s+', ' ', s_narr).strip()
                # Khử các từ nối/giới từ/tính từ sở hữu bị cắt cụt ở cuối câu
                while _dangling_end_re.search(s_narr):
                    s_narr = _dangling_end_re.sub("", s_narr).strip()
                if idx_s == len(scenes) - 1:
                    s_narr = s_narr.rstrip(".,-—…")
                    if s_narr and not s_narr.endswith((".", "!", "?")):
                        s_narr += "."
                sc_obj["narration"] = s_narr

        # ── 2.85. VIRAL HOOK RETENTION TOURNAMENT (ĐẤU TRƯỜNG HOOK 0.0s TỐI ƯU STAY-TO-WATCH >= 75%) ──
        def _strip_variant_prefix(val: str) -> str:
            if not val or not isinstance(val, str):
                return ""
            cleaned = re.sub(
                r'^(?:(?:hook|title|tiêu\s*đề)\s*)?(?:variant|option|ver|version|biến\s*thể| mẫu)\s*#?\d+\s*(?:\([^)]*\))?\s*[:\-–—]\s*',
                '',
                val.strip(),
                flags=re.IGNORECASE,
            ).strip()
            return cleaned.strip('"\'')

        if raw_data.get("title"):
            raw_data["title"] = _strip_variant_prefix(str(raw_data["title"]))
        if isinstance(raw_data.get("title_variants"), list):
            raw_data["title_variants"] = [
                _strip_variant_prefix(str(v))
                for v in raw_data["title_variants"]
                if v and _strip_variant_prefix(str(v))
            ]
        if raw_data.get("hook"):
            raw_data["hook"] = _strip_variant_prefix(str(raw_data["hook"]))
        if isinstance(raw_data.get("hook_variants"), list):
            raw_data["hook_variants"] = [
                _strip_variant_prefix(str(v))
                for v in raw_data["hook_variants"]
                if v and _strip_variant_prefix(str(v))
            ]

        if scenes and isinstance(scenes[0], dict):
            if scenes[0].get("narration"):
                scenes[0]["narration"] = _strip_variant_prefix(str(scenes[0]["narration"]))
            hook_candidates: List[str] = []
            for h_cand in [scenes[0].get("narration"), raw_data.get("hook"), *(raw_data.get("hook_variants") or [])]:
                if h_cand and isinstance(h_cand, str):
                    hc = _strip_variant_prefix(h_cand)
                    if hc and hc not in hook_candidates:
                        hook_candidates.append(hc)
            if len(hook_candidates) >= 2:
                def _score_hook_retention(h_str: str) -> float:
                    h_score = 50.0
                    words_h = re.findall(r'\b\w+\b', h_str)
                    w_len = len(words_h)
                    # Độ dài vàng cho Hook 0.0s (5 - 10 từ ~ 1.8s - 2.6s): Đủ 1 câu khẳng định sắc lẹm
                    if 5 <= w_len <= 10:
                        h_score += 22.0
                    elif w_len > 12 or w_len < 4:
                        h_score -= 20.0
                    # Chứa số liệu / tốc độ / lực cắn / thời gian cụ thể
                    if re.search(r'\d', h_str):
                        h_score += 18.0
                    # Thưởng lớn cho công thức "A vs B" / Đối đầu bất đối xứng (pt-BR & Wild Odyssey Formula)
                    if re.search(r'\b(?:vs\.?|versus|against|contra|outsmart|crushes|destroys|beats|backs down)\b', h_str, flags=re.IGNORECASE):
                        h_score += 18.0
                    # Chứa động từ hành động mạnh / nghịch lý / sinh tồn (EN / VI / PT)
                    power_hook_words = (
                        "never", "deadly", "crush", "swallow", "silent", "seconds", "freeze", "blind", "strike", "bite",
                        "venom", "paradox", "impossible", "mistake", "alive", "shivering", "refused", "terror", "abyss",
                        "weapon", "trap", "manipulat", "narcissist", "toxic", "lie", "liar", "secret", "warning",
                        "tử thần", "ngay lập tức", "nghịch lý", "cắn", "nuốt", "đóng băng", "run rẩy", "tuyệt đối", "bí mật",
                        "jamais", "mortal", "esmagar", "engolir", "silêncio", "segundos", "erro", "impossível", "veneno", "abismo"
                    )
                    h_low = h_str.lower().strip()
                    if any(pw in h_low for pw in power_hook_words):
                        h_score += 16.0
                    # Phạt nặng nếu mở đầu bằng câu hỏi thụ động Yes/No hoặc câu hỏi sáo rỗng làm tụt Stayed-to-Watch
                    if re.match(
                        r'^(?:stop scrolling|did you know|have you ever|did they|did you|do you|why did|what did|what roared|what made|is it|are you|can you|bạn có biết|você sabia)\b',
                        h_low,
                    ):
                        h_score -= 38.0
                    if h_str.strip().endswith("?"):
                        h_score -= 18.0
                    return h_score

                current_sc1 = str(scenes[0].get("narration") or "").strip()
                best_hook = max(hook_candidates, key=_score_hook_retention)
                if (
                    best_hook != current_sc1
                    and 4 <= len(re.findall(r'\b\w+\b', best_hook)) <= 10
                    and _score_hook_retention(best_hook) >= _score_hook_retention(current_sc1) + 6.0
                ):
                    logger.info(
                        f"[ContentAgent] 🔥 Hook Retention Tournament chọn Hook 0.0s mạnh nhất: "
                        f"'{best_hook}' (thay cho '{current_sc1}')"
                    )
                    scenes[0]["narration"] = best_hook
                    scenes[0]["highlight_words"] = _auto_extract_scene_highlights(best_hook)


        # Cập nhật lại full_narration & hook để đồng bộ 100% với Cảnh 1 đã tối ưu nhịp
        raw_data["full_narration"] = " ".join(s.get("narration", "") for s in scenes if isinstance(s, dict))
        if scenes and isinstance(scenes[0], dict) and scenes[0].get("narration"):
            raw_data["hook"] = str(scenes[0]["narration"]).strip()
        elif "hook" not in raw_data or not raw_data["hook"]:
            raw_data["hook"] = raw_data.get("title", "")

        # ── 2.88. CỔNG QUYẾT ĐỊNH HOOK CỦA GEMINI: TẠO VIDEO ("video") HAY TẠO CẢNH ("scene") ──
        raw_h_mode = str(raw_data.get("hook_visual_mode") or "").strip().lower()
        raw_is_abs = raw_data.get("is_abstract_keyword")
        if raw_h_mode in ("scene", "image", "ai_image", "photo", "traditional"):
            raw_data["hook_visual_mode"] = "scene"
            raw_data["is_abstract_keyword"] = True
        elif raw_h_mode in ("video", "short", "shorts", "clip"):
            raw_data["hook_visual_mode"] = "video"
            raw_data["is_abstract_keyword"] = False if raw_is_abs is None else bool(raw_is_abs)
        elif isinstance(raw_is_abs, bool):
            raw_data["is_abstract_keyword"] = raw_is_abs
            raw_data["hook_visual_mode"] = "scene" if raw_is_abs else "video"

        if scenes and isinstance(scenes, list):
            default_climax_id = min(max(3, len(scenes) // 2), max(len(scenes) - 1, 1))
            try:
                c_id = int(raw_data.get("climax_scene_id") or default_climax_id)
                if c_id < 2 or c_id > len(scenes):
                    c_id = default_climax_id
            except Exception:
                c_id = default_climax_id
            raw_data["climax_scene_id"] = c_id

        raw_h_queries = raw_data.get("hook_search_queries")
        if isinstance(raw_h_queries, list):
            raw_data["hook_search_queries"] = [str(q).strip() for q in raw_h_queries if str(q).strip()][:2]
        elif isinstance(raw_h_queries, str) and raw_h_queries.strip():
            raw_data["hook_search_queries"] = [q.strip() for q in raw_h_queries.split(",") if q.strip()][:2]

        # ── 2.9. OPUSCLIP / VIDIQ VIRAL CTR TITLE TOURNAMENT (ĐẤU TRƯỜNG TIÊU ĐỀ TỐI ƯU CTR) ──
        raw_variants = raw_data.get("title_variants")
        candidates: List[str] = []
        if raw_data.get("title"):
            candidates.append(_strip_variant_prefix(str(raw_data["title"])))
        if isinstance(raw_variants, list):
            for v in raw_variants:
                v_clean = _strip_variant_prefix(str(v)) if v else ""
                if v_clean and v_clean not in candidates:
                    candidates.append(v_clean)
        niche_norm = str(channel_niche or "").strip().lower()
        topic_norm = str(raw_data.get("topic") or topic or "").strip().lower()
        is_what_if_context = (
            niche_norm == "alternate_history"
            or topic_norm.startswith(("what if", "e se ", "nếu "))
            or "what if" in topic_norm
        )
        if len(candidates) >= 2:
            def _score_title_ctr(t_str: str) -> float:
                score = 50.0
                clean_t = re.sub(r'#\w+', '', _strip_variant_prefix(t_str)).strip()
                c_len = len(clean_t)
                # Chiều dài vàng trên Shorts Shelf Mobile: 26 - 56 ký tự (không bị cắt '...')
                if 26 <= c_len <= 56:
                    score += 20.0
                elif c_len > 60:
                    score -= 22.0
                # Chứa con số / dữ kiện định lượng / đối đầu trực diện
                if re.search(r'\d', clean_t):
                    score += 12.0
                if re.search(r'\b(vs\.?|versus|hay|or)\b', clean_t, flags=re.IGNORECASE):
                    score += 10.0
                # Chứa từ khóa kích hoạt tò mò / cảnh báo / giả định (Curiosity Gap & Counterfactual Triggers)
                ctr_triggers = (
                    "what if", "why", "never", "secret", "truth", "sign", "until", "mistake", "hidden", "actually", "refused",
                    "nếu", "tại sao", "đừng", "bí mật", "sự thật", "dấu hiệu", "cho đến khi", "sai lầm", "ẩn giấu",
                    "e se", "por que", "nunca", "segredo", "verdade", "erro", "jamais"
                )
                t_low = clean_t.lower()
                if any(k in t_low for k in ctr_triggers):
                    score += 14.0
                # Bảo toàn tuyệt đối cấu trúc "What If..." cho kênh Alternate History & "What If?"
                if is_what_if_context and any(w in t_low for w in ("what if", "e se ", "nếu ")):
                    score += 26.0
                if "?" in clean_t or "!" in clean_t:
                    score += 5.0
                return score

            best_title = max(candidates, key=_score_title_ctr)
            if best_title != raw_data.get("title"):
                logger.info(f"[ContentAgent] 🎯 CTR Title Tournament chọn tiêu đề tối ưu: '{best_title}' (thay cho '{raw_data.get('title')}')")
                raw_data["title"] = best_title

        if "tags" not in raw_data or not raw_data["tags"]:
            raw_data["tags"] = ["shorts", "viral"]
        elif isinstance(raw_data["tags"], str):
            raw_data["tags"] = [t.strip() for t in raw_data["tags"].split(",") if t.strip()]
        if "total_duration_estimate" not in raw_data or not raw_data["total_duration_estimate"]:
            raw_data["total_duration_estimate"] = round(sum(s.get("estimated_seconds", 3.2) for s in scenes if isinstance(s, dict)) or 35.0, 1)
        if "topic" not in raw_data or not raw_data["topic"]:
            raw_data["topic"] = raw_data.get("title", "Shorts Video")
        if "thumbnail_text" not in raw_data or not raw_data["thumbnail_text"]:
            try:
                from app.services.thumbnail_service import ThumbnailService
                raw_data["thumbnail_text"] = ThumbnailService().derive_curiosity_text(topic=raw_data.get("topic", ""), viral_title=raw_data.get("title", ""))
            except Exception:
                raw_data["thumbnail_text"] = "SỰ THẬT TÀN KHỐC!"

        # ── 3. BÌNH LUẬN MỒI GHIM ĐẦU TRANG (PINNED COMMENT BAIT) TỐI ƯU TƯƠNG TÁC ──
        comb_all = f"{niche_norm} {str(raw_data.get('topic', ''))} {str(raw_data.get('title', ''))}".lower()
        is_pt_lang = any(w in comb_all for w in ("você", "não", "por que", "oceano", "selva", "onça", "jacaré", "segredo", "ilha", "caverna", "leões", "ratel", "harpia"))
        is_vn = any("\u0100" <= c <= "\u1EF9" for c in comb_all) and not is_pt_lang
        if "pinned_comment" not in raw_data or not str(raw_data.get("pinned_comment", "")).strip():
            if niche_norm == "animal_rescue" or any(k in comb_all for k in ("rescue", "chó", "mèo", "pet", "cứu hộ", "puppy", "kitten", "stray")):
                raw_data["pinned_comment"] = (
                    "Chấm điểm màn lột xác của bé từ 1 đến 10 nhé! 🥺 Thả ❤️ bên dưới để chúc bé luôn hạnh phúc!"
                    if is_vn else
                    "Rate this sweet baby's transformation from 1 to 10! 🥺 Drop a ❤️ in the comments if this made your day!"
                )
            elif niche_norm == "alternate_history" or any(k in comb_all for k in ("lịch sử", "history", "what if", "thế chiến", "war", "empire", "roman", "alexandria")):
                raw_data["pinned_comment"] = (
                    "Nếu là bạn: Chọn (1) Giữ nguyên lịch sử hay (2) Thay đổi tất cả? Bình chọn 1 hoặc 2 bên dưới 👇"
                    if is_vn else
                    "Quick Poll: Would you choose (1) Keep our timeline or (2) Rewrite history? Drop 1 or 2 below! 👇"
                )
            elif niche_norm == "deep_sea_earth_mysteries" or any(k in comb_all for k in ("deep sea", "biển sâu", "ocean", "đại dương", "mystery", "bí ẩn", "abyss", "caverna", "ilha", "oceano")):
                raw_data["pinned_comment"] = (
                    "Enquete rápida: Você acha que isso é (1) Criatura Desconhecida ou (2) Fenômeno Natural? Digite 1 ou 2 nos comentários! 👇"
                    if is_pt_lang else (
                        "Theo bạn đây là (1) Sinh vật chưa được khám phá hay (2) Hiện tượng tự nhiên? Gõ 1 hoặc 2 bên dưới 👇"
                        if is_vn else
                        "Quick Poll: Is this (1) An Undiscovered Creature or (2) A Natural Anomaly? Drop 1 or 2 below! 👇"
                    )
                )
            elif niche_norm == "wildlife_predators" or any(k in comb_all for k in ("săn mồi", "predator", "wildlife", "dã thú", "báo", "sư tử", "hổ", "cheetah", "leopard", "eagle", "onça", "sucuri", "jacaré", "ratel", "hippo", "orca")):
                raw_data["pinned_comment"] = (
                    "Quem vence esse duelo mortal no 1x1? Digite seu voto nos comentários! 👇🔥"
                    if is_pt_lang else (
                        "Kèo sinh tử 1v1 này bạn chọn phe nào? Để lại bình chọn bên dưới 👇🔥"
                        if is_vn else
                        "Who takes the crown in this 1v1 apex showdown? Drop your vote in the comments below! 👇🔥"
                    )
                )
            elif niche_norm == "psychology" or any(k in comb_all for k in ("tâm lý", "psychology", "manipulat", "thao túng", "mindset", "gaslight", "liar", "dark psychology")):
                raw_data["pinned_comment"] = (
                    "Bạn đã từng gặp kiểu người này ngoài đời thực chưa? Gõ (1) Đã từng hoặc (2) Chưa bao giờ bên dưới nhé 👇"
                    if is_vn else
                    "Have you ever dealt with someone who used Sign #2 in real life? Drop (1) YES or (2) NO below! 👇"
                )
            else:
                raw_data["pinned_comment"] = (
                    "Chi tiết nào khiến bạn bất ngờ nhất? Hãy để lại bình luận của bạn bên dưới nhé 👇"
                    if is_vn else
                    "Which moment shocked you the most? Drop your thoughts in the comments below 👇"
                )

        # ── 4. NICHE-AWARE MINI-EBOOK DESCRIPTION BREAKDOWN (CHỐNG RÒ RỈ METADATA CHÉO NGÁCH 100%) ──
        raw_desc = str(raw_data.get("description") or "").strip()
        is_psych_niche = (
            niche_norm == "psychology"
            or (not niche_norm or niche_norm == "general")
            and any(k in comb_all for k in ("psychology", "tâm lý", "manipulat", "gaslight", "narcissist", "liar"))
        )
        if not is_psych_niche and raw_desc:
            raw_desc = re.sub(r'#(?:psychology|darkpsychology|selfmastery|mindcontrol)\b', '', raw_desc, flags=re.IGNORECASE).strip()
            raw_desc = raw_desc.replace("🧠 KEY PSYCHOLOGICAL BREAKDOWN:", "📌 KEY VIDEO BREAKDOWN:")
            raw_data["description"] = raw_desc

        if len(raw_desc) < 180 and raw_data.get("scenes"):
            sc_list = raw_data.get("scenes") or []
            title_str = str(raw_data.get("title") or "").strip()
            hook_str = str(raw_data.get("hook") or (sc_list[0].get("narration", "") if sc_list else "")).strip()
            mid_idx = max(1, len(sc_list) // 2)
            act2_str = str(sc_list[min(2, len(sc_list) - 1)].get("narration", "")).strip() if len(sc_list) > 2 else ""
            act3_str = str(sc_list[mid_idx].get("narration", "")).strip() if len(sc_list) > mid_idx else ""
            act4_str = str(sc_list[max(0, len(sc_list) - 2)].get("narration", "")).strip() if len(sc_list) >= 2 else ""

            if niche_norm == "alternate_history" or any(k in comb_all for k in ("what if", "alternate history", "empire", "roman", "alexandria")):
                default_tags = ["#shorts", "#alternatehistory", "#whatif", "#history"]
                header_block = (
                    f"⏳ ALTERNATE HISTORY TIMELINE BREAKDOWN:\n"
                    f"• The Point of Divergence: {act2_str}\n"
                    f"• The Historical Domino Effect: {act3_str}\n"
                    f"• How Our Modern World Changes: {act4_str}"
                )
            elif niche_norm == "deep_sea_earth_mysteries" or any(k in comb_all for k in ("deep sea", "ocean", "mystery", "caverna", "ilha", "oceano", "abyss")):
                if is_pt_lang:
                    default_tags = ["#shorts", "#misterios", "#oceano", "#ciencia", "#curiosidades"]
                    header_block = (
                        f"🌊 ANÁLISE DO MISTÉRIO E CIÊNCIA:\n"
                        f"• O Fenômeno Oculto: {act2_str}\n"
                        f"• A Explicação Científica: {act3_str}\n"
                        f"• A Revelação Final: {act4_str}"
                    )
                else:
                    default_tags = ["#shorts", "#mysteries", "#deepsea", "#science", "#unexplained"]
                    header_block = (
                        f"🌊 SCIENTIFIC & MYSTERY BREAKDOWN:\n"
                        f"• The Unexplained Anomaly: {act2_str}\n"
                        f"• The Hidden Mechanism: {act3_str}\n"
                        f"• The Final Revelation: {act4_str}"
                    )
            elif niche_norm == "wildlife_predators" or any(k in comb_all for k in ("predator", "wildlife", "onça", "jacaré", "ratel", "harpia", "hippo", "orca", "leopard", "anaconda")):
                if is_pt_lang:
                    default_tags = ["#shorts", "#vidaanimal", "#natureza", "#predadores", "#curiosidades"]
                    header_block = (
                        f"🐆 ANÁLISE DO CONFRONTO SELVAGEM:\n"
                        f"• O Ataque Inicial: {act2_str}\n"
                        f"• A Vantagem Biomecânica: {act3_str}\n"
                        f"• O Veredito da Natureza: {act4_str}"
                    )
                else:
                    default_tags = ["#shorts", "#wildlife", "#nature", "#animals", "#predators"]
                    header_block = (
                        f"🦈 APEX WILDLIFE BREAKDOWN:\n"
                        f"• The Opening Strike: {act2_str}\n"
                        f"• Biomechanical Advantage: {act3_str}\n"
                        f"• Survival Verdict: {act4_str}"
                    )
            elif niche_norm == "animal_rescue" or any(k in comb_all for k in ("rescue", "puppy", "kitten", "stray", "shelter")):
                default_tags = ["#shorts", "#animalrescue", "#dogrescue", "#wholesome", "#puppies"]
                header_block = (
                    f"🐾 RESCUE TRANSFORMATION STORY:\n"
                    f"• The Moment Found: {act2_str}\n"
                    f"• The Turning Point of Trust: {act3_str}\n"
                    f"• A Brand New Life: {act4_str}"
                )
            elif is_vn:
                default_tags = ["#shorts", "#tamlyhoc", "#kynangsong", "#phattrienbanthan"]
                header_block = (
                    f"🧠 3 ĐIỂM CỐT LÕI TRONG VIDEO:\n"
                    f"1️⃣ Dấu hiệu nhận diện: {act2_str}\n"
                    f"2️⃣ Cơ chế tâm lý ngầm: {act3_str}\n"
                    f"3️⃣ Đòn hóa giải thực chiến: {act4_str}"
                )
            else:
                default_tags = ["#shorts", "#psychology", "#darkpsychology", "#selfmastery"]
                header_block = (
                    f"🧠 KEY PSYCHOLOGICAL BREAKDOWN:\n"
                    f"• The Hidden Pattern: {act2_str}\n"
                    f"• The Neurological Mechanism: {act3_str}\n"
                    f"• Tactical Counter-Move: {act4_str}"
                )

            raw_tags_filtered = [
                f"#{str(t).lstrip('#').replace(' ', '')}"
                for t in (raw_data.get("tags") or ["shorts"])
                if str(t).strip()
                and (is_psych_niche or str(t).lstrip('#').lower() not in ("psychology", "darkpsychology", "selfmastery"))
            ]
            tags_line = " ".join(dict.fromkeys(raw_tags_filtered + default_tags))
            raw_data["description"] = (
                f"{title_str}\n\n"
                f"⚡ {hook_str}\n\n"
                f"{header_block}\n\n"
                f"{raw_desc}\n\n{tags_line}"
            ).strip()

        return ScriptContract(**raw_data)

    def _build_channel_context_memory(
        self,
        channel_niche: str,
        topic: str,
        channel_id: Optional[str] = None,
    ) -> str:
        """
        Hệ thống Trí nhớ Ngữ cảnh Kênh & Phản Hồi Dữ Liệu Thật Hàng Tuần (Weekly Real-Data Closed Loop):
        1. Mỗi tuần tự động truy vấn từ DB (`PublishedVideo`, `VideoJob`) để đưa:
           - Top 3 video thắng (Winners) kèm Views, Tỷ lệ xem hết (APV %), Tỷ lệ ở lại xem (Stayed %) & Tỷ lệ vuốt bỏ (Swipe-Away %) + câu Hook mở đầu.
           - Bottom 3 video thua (Losers) kèm chỉ số vuốt bỏ thật để LLM tránh lặp lại mô-típ mở đầu yếu.
           - 8 tiêu đề gần nhất đã sản xuất (chống lặp góc tiếp cận, câu mở đầu và từ khóa bão hòa).
        2. Áp dụng công thức chiến thắng của các kênh `pt-BR` và `Wild Odyssey` (Cấu trúc đối đầu trực diện `"A vs B"` + Câu khẳng định gây sốc kèm thông số vật lý ở giây 0.0s) sang toàn bộ các kênh `EN`.
        """
        top_winners: List[str] = []
        bottom_losers: List[str] = []
        recent_titles: List[str] = []
        saturated_terms: List[str] = []
        channel_avg_swipe: float = 30.0
        week_label: str = ""
        canon_cid: Optional[str] = None

        try:
            from app.database.models import get_db, PublishedVideo, VideoJob, normalize_channel_id
            from app.services.smart_scheduler import resolve_channel_family
            from app.services.video_library import get_weekly_winners_and_losers

            target_ids: List[str] = []
            matched_handle: Optional[str] = None
            canon_cid = normalize_channel_id(channel_id) if channel_id else None
            niche_low = (channel_niche or "").strip().lower()
            for ch in settings.get_channels_config():
                cid = ch.get("id")
                ch_niche = str(ch.get("niche") or "").strip().lower()
                if (canon_cid and cid == canon_cid) or (not canon_cid and ch_niche == niche_low):
                    if cid:
                        if not canon_cid:
                            canon_cid = cid
                        target_ids.extend(resolve_channel_family(cid))
                    if not matched_handle and ch.get("youtube_handle"):
                        matched_handle = str(ch.get("youtube_handle")).strip()

            if canon_cid and not target_ids:
                target_ids.extend(resolve_channel_family(canon_cid))

            from app.services.channel_intelligence import is_off_niche_video_title, extract_saturated_channel_concepts

            # 1. Lấy tự động 3 Video Thắng (Top 3 Winners) & 3 Video Thua (Bottom 3 Losers) theo tuần từ DB thật
            if canon_cid:
                weekly_perf = get_weekly_winners_and_losers(canon_cid, top_k=3)
                week_label = weekly_perf.get("week_key", "")
                channel_avg_swipe = float(weekly_perf.get("avg_swipe_away_pct", 30.0))
                for idx, w in enumerate(weekly_perf.get("winners", []), 1):
                    w_title = w.get("title", "")
                    if w_title and not is_off_niche_video_title(w_title, niche_low):
                        w_hook = w.get("hook_text") or w_title
                        w_ret = (
                            f'Stayed: {w["stayed_to_watch_pct"]:.1f}% | Swipe-Away: {w["swipe_away_pct"]:.1f}% | Completion (APV): {w["avg_view_percentage"]:.1f}%'
                            if w.get("stayed_to_watch_pct") is not None and w.get("avg_view_percentage") is not None
                            else "⏳ Analytics pending 24-48h"
                        )
                        top_winners.append(
                            f'  * [WINNER #{idx}] Title: "{w_title}" | Scene 1 Hook: "{w_hook}" '
                            f'-> {w["views"]:,} views | {w_ret}'
                        )
                for idx, l in enumerate(weekly_perf.get("losers", []), 1):
                    l_title = l.get("title", "")
                    if l_title:
                        l_hook = l.get("hook_text") or l_title
                        l_ret = (
                            f'Stayed: {l["stayed_to_watch_pct"]:.1f}% | Swipe-Away: {l["swipe_away_pct"]:.1f}% | Completion (APV): {l["avg_view_percentage"]:.1f}%'
                            if l.get("stayed_to_watch_pct") is not None and l.get("avg_view_percentage") is not None
                            else "⏳ Analytics pending 24-48h"
                        )
                        bottom_losers.append(
                            f'  * [LOSER #{idx}] Title: "{l_title}" | Scene 1 Hook: "{l_hook}" '
                            f'-> {l["views"]:,} views | {l_ret} '
                            f'(HIGH SWIPE-AWAY — DO NOT REPEAT THIS SLOW/VAGUE HOOK PATTERN!)'
                        )

            if target_ids:
                unique_ids = list(dict.fromkeys(target_ids))
                with get_db() as db:
                    rec_pub = (
                        db.query(PublishedVideo)
                        .filter(PublishedVideo.channel_id.in_(unique_ids))
                        .order_by(PublishedVideo.published_at.desc())
                        .limit(10)
                        .all()
                    )
                    for rp in rec_pub:
                        if rp.title and rp.title.strip() not in recent_titles:
                            recent_titles.append(rp.title.strip())

                    rec_jobs = (
                        db.query(VideoJob)
                        .filter(
                            VideoJob.target_channel_id.in_(unique_ids),
                            VideoJob.youtube_video_id.isnot(None),
                            VideoJob.youtube_video_id != "",
                        )
                        .order_by(VideoJob.created_at.desc())
                        .limit(10)
                        .all()
                    )
                    for rj in rec_jobs:
                        if rj.title and rj.title.strip() not in recent_titles:
                            recent_titles.append(rj.title.strip())

            # Closed-Loop Live YouTube Winner DNA Sync: Nếu DB cục bộ chưa cập nhật đủ top_winners,
            # tự động đồng bộ từ thống kê kênh YouTube thực tế (có bộ nhớ đệm TTL 30 phút chống chậm pipeline)
            if not top_winners and matched_handle:
                import time as _time
                cache_attr = "_live_yt_winner_cache"
                cache_store = getattr(self.__class__, cache_attr, None)
                if cache_store is None:
                    cache_store = {}
                    setattr(self.__class__, cache_attr, cache_store)
                now_ts = _time.time()
                cached_entry = cache_store.get(matched_handle)
                if not cached_entry or (now_ts - cached_entry.get("ts", 0)) > 1800:
                    try:
                        from app.services.youtube_analytics import fetch_channel_live_stats
                        live_res = fetch_channel_live_stats(matched_handle)
                        if live_res.get("success") and live_res.get("videos"):
                            cache_store[matched_handle] = {"ts": now_ts, "videos": live_res["videos"]}
                            cached_entry = cache_store[matched_handle]
                    except Exception:
                        pass
                if cached_entry and cached_entry.get("videos"):
                    for v_info in cached_entry["videos"][:3]:
                        v_title = str(v_info.get("title") or "").strip()
                        v_views = int(v_info.get("views_num") or 0)
                        if v_title and v_views >= 20 and not is_off_niche_video_title(v_title, niche_low):
                            top_winners.append(f'  * "{v_title}" ({v_views:,} live YouTube views)')
                    for v_info in list(reversed(cached_entry["videos"]))[:3]:
                        v_title = str(v_info.get("title") or "").strip()
                        v_views = int(v_info.get("views_num") or 0)
                        if v_title:
                            bottom_losers.append(f'  * "{v_title}" ({v_views:,} views — High Swipe-Away Loser)')
                    for v_info in cached_entry["videos"][:8]:
                        v_title = str(v_info.get("title") or "").strip()
                        if v_title and v_title not in recent_titles:
                            recent_titles.append(v_title)

            if len(recent_titles) >= 2:
                saturated_terms = extract_saturated_channel_concepts(recent_titles, min_frequency=2)
        except Exception as ex_mem:
            logger.debug(f"[ContentAgent] Không thể đọc episodic memory cho {channel_niche}: {ex_mem}")

        memory_sections = [
            "═══════════════════════════════════════════════════════════",
            f"🧠 WEEKLY REAL-DATA ANALYTICS LOOP ({week_label or 'LIVE'}) — CHANNEL SWIPE-AWAY BASELINE: {channel_avg_swipe:.1f}%:",
        ]
        if top_winners:
            memory_sections.append(
                "🏆 TOP 3 WINNING VIDEOS OF THIS CHANNEL (High Stayed-to-Watch % & High Completion APV % — Replicate their direct confrontation & concrete physical numbers with a FRESH topic):\n"
                + "\n".join(top_winners[:3])
            )
        if bottom_losers:
            memory_sections.append(
                "💀 BOTTOM 3 LOSING VIDEOS OF THIS CHANNEL (High Swipe-Away % & Low Retention — STRICTLY AVOID their slow setup, generic phrasing, or passive opening hooks):\n"
                + "\n".join(bottom_losers[:3])
            )
        memory_sections.append(
            """🔥 MANDATORY WINNING FORMULA FROM pt-BR & WILD ODYSSEY (APPLY STRICTLY TO ALL EN & pt-BR CHANNELS):
1. "A vs B" ASYMMETRIC CONFRONTATION FORMULA:
   - Frame both the Title and Scene 1 around a high-stakes, visceral confrontation between two opposing forces/entities ("Entity A vs. Entity B", "Small Underdog vs. Apex Giant", "4-Second Silence vs. Narcissist Trap", "3,600m Ice Sheet vs. Military Sonar", "Stray Dog vs. Highway Traffic").
2. SHOCKING DECLARATIVE STATEMENT AT 0.0s (SCENE 1 = 5 TO 8 WORDS MAX):
   - Open Scene 1 with a blunt, undeniable statement of fact or survival warning backed by an extreme physical/numerical metric (PSI, mph, meters, seconds, %, kg).
   - ZERO passive introductions, ZERO rhetorical or Yes/No questions ("Did you know...", "Have you ever...")."""
        )
        if recent_titles:
            memory_sections.append(
                "🚫 RECENTLY PRODUCED VIDEOS ON THIS CHANNEL (DO NOT repeat these exact titles, opening hook sentences, or examples):\n"
                + "\n".join(f'  - "{t}"' for t in recent_titles[:8])
            )
        if saturated_terms:
            memory_sections.append(
                f"⚠️ SATURATED CONCEPTS ON THIS CHANNEL (Avoid overusing these repeated words/stats): {', '.join(saturated_terms[:6])}"
            )

        # Mini-RAG Semantic Script & Visual Metadata Memory (tránh lặp nội dung kịch bản + từ khóa hình ảnh cũ)
        if canon_cid:
            try:
                from app.services.video_memory_rag import get_video_memory_rag
                rag_ctx = get_video_memory_rag().build_rag_prompt_context(canon_cid, topic, top_k=4)
                if rag_ctx:
                    memory_sections.append(rag_ctx)
            except Exception as ex_rag:
                logger.debug(f"[ContentAgent] Mini-RAG context warning: {ex_rag}")

        try:
            from app.services.niche_manager import get_niche
            n_prof = get_niche(channel_niche)
            topic_bank_2026 = (n_prof.get("script_blueprint") or {}).get("viral_topic_bank_2026") or []
            if topic_bank_2026:
                memory_sections.append(
                    "🔥 2026 MILLION-VIEW COMPETITOR BENCHMARK HOOKS FOR THIS NICHE:\n"
                    + "\n".join(f'  * "{tb}"' for tb in topic_bank_2026[:4])
                )
        except Exception:
            pass

        memory_sections.append(
            f"""🌐 ZERO-SHOT MULTI-NICHE COGNITIVE ADAPTATION (Niche: '{channel_niche}' | Topic: '{topic}'):
- Even when scaling to brand-new channels or custom sub-niches, autonomously deduce the target audience's #1 psychological trigger (e.g. Fear of Deception, Awe of Unseen Scale, Empathy & Moral Justice, Survival Instinct, or Forbidden Status/Wealth).
- SCENE-LEVEL AUDIO-VISUAL DIRECTION (MANDATORY FOR EVERY SCENE):
  1. `highlight_words`: Pick 1-2 most emotionally or factually explosive words/numbers directly from that scene's `narration` (e.g. ["BLIND", "0.2"], ["4,000", "CRUSHED"], ["BETRAYED"]). Our subtitle engine will render these exact words in glowing Neon Cyan/Yellow and trigger synchronized micro-SFX foley!
  2. `overlay_badge`: Provide a clean, NO-EMOJI uppercase broadcast HUD stamp (2-4 words in brackets, e.g. "[ // HIDDEN SIGN #1 // ]", "[ // 12-SECOND TWIST // ]", "[ // FATAL MISTAKE // ]") on Scene 1 and Scene 4/5 (the 12-second Mid-Video Re-Hook) to reset viewer attention. Set `null` on other scenes.
  3. `camera_motion`: Dynamically vary camera movement across consecutive scenes (`"zoom_in_fast"` for Scene 1 Hook & climax, alternating `"ken_burns_zoom_out"`, `"pan_right"`, `"ken_burns_zoom_in"`, `"pan_left"`).
  4. `sfx_cue`: Choose the exact sound design cue matching the scene's emotion (`"impact_boom"`, `"whoosh_heavy"`, `"riser_tension"`, `"heartbeat_thump"`, `"glitch"`, `"reveal"`)."""
        )
        memory_sections.append("═══════════════════════════════════════════════════════════")
        return "\n".join(memory_sections)


    def generate_script(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        cooldown_entities: Optional[List[str]] = None,
        min_acceptable_score: Optional[float] = None,
        **kwargs
    ) -> ScriptContract:
        """
        Sinh kịch bản video Shorts viral tối ưu thuật toán giữ chân (AVD > 80%, thời lượng vàng 35-55 giây, 7-10 cảnh).
        Nhịp cảnh dồn dập (3.5-5.0s/cảnh), Hook 3s đầu đảo ngược định kiến, Mid-Video Re-Hook ở Cảnh 4 (giây 12-15),
        Payoff sâu sắc ở Cảnh 5-8 và vòng lặp vô tận (Infinite Shorts Loop).
        """
        director_block = ""
        if director_notes and len(director_notes.strip()) > 3:
            director_block = f"""
═══════════════════════════════════════════════════════════
🎯 CREATOR'S CUSTOM CREATIVE DIRECTION (HIGHEST PRIORITY):
The creator provided the following specific guidance for this video:
"{director_notes.strip()}"
You MUST strictly align the angle, narrative tone, character focus, and visual atmosphere with these instructions!
═══════════════════════════════════════════════════════════
"""

        from app.services.niche_manager import get_niche, build_script_prompt_instructions
        niche_profile = get_niche(channel_niche)
        niche_instructions = build_script_prompt_instructions(niche_profile, language=language)
        lang_instruction = build_language_integrity_instruction(language)
        channel_memory_block = self._build_channel_context_memory(
            channel_niche=channel_niche,
            topic=topic,
            channel_id=kwargs.get("channel_id"),
        )
        lang_clean = (language or "en").strip().lower()
        if lang_clean.startswith("vi"):
            target_lang_display = "TIẾNG VIỆT"
        elif lang_clean.startswith("pt"):
            target_lang_display = "PORTUGUÊS BRASILEIRO"
        else:
            target_lang_display = "ENGLISH"

        duration_spec = "28 ĐẾN 36 GIÂY VÀNG TỐI ƯU APV > 80% & STAYED-TO-WATCH > 65% (6 ĐẾN 8 PHÂN CẢNH SẮC GỌN, 72 ĐẾN 98 TỪ — CẢNH 1 HOOK 5-8 TỪ CỰC GẮT, DẪN DẮT MƯỢT MÀ CÓ ĐẦU CÓ ĐUÔI, KHÔNG DÀI LÊ THÊ)"
        word_range_en = "72 to 98 words (28-36s High-APV Golden Duration across 6-8 crisp, complete scenes; Scene 1 Hook MUST be 5-8 words ~2.0s)"

        prompt = f"""You are an elite YouTube Shorts Master Showrunner, Behavioral Storyteller, and Viral Retention Architect for the channel '{niche_profile.get('name')}' (niche: '{channel_niche}').
Topic: '{topic}'
Target Language: {language} ({target_lang_display})
{lang_instruction}
{director_block}
{channel_memory_block}
{niche_instructions}
═══════════════════════════════════════════════════════════
MASTER-CLASS YOUTUBE SHORTS ARCHITECTURE (30–40 SECONDS | 85–120 WORDS | SMOOTH & COMPLETE STORYTELLING)
YouTube Shorts rewards TWO metrics simultaneously:
1. Viewed vs. Swiped-Away > 75%: Achieved by a sharp Scene 1 Hook (5–9 words) + an engaging opening visual.
2. Smooth, Coherent, Full-Bodied Narrative (85–120 words, 30–40s):
   - NEVER write an abrupt, choppy, or truncated script ("cụt lủn")! Let the sentences flow naturally into one another with smooth transitions, clear context, and complete explanations so the viewer feels deeply satisfied.
   - Flexible sentence length per scene (8 to 16 words per body scene) — prioritize natural storytelling rhythm and emotional flow over rigid word counting!
   - Every scene MUST be a 100% grammatically complete sentence. NEVER chop one sentence across two scenes!
═══════════════════════════════════════════════════════════
1. THE MANDATORY 5-ACT DEEP STORYTELLING BLUEPRINT (target: {word_range_en}):
   - DURATION & SCENE COUNT: {duration_spec}.
   - YOU MUST EXECUTE ALL 5 ACTS SMOOTHLY IN ORDER across 7 to 9 scenes:

     * ACT 1 — THE VISCERAL HOOK (Scene 1 | 5 to 9 words):
       - Deliver an immediate, personal, high-stakes truth, 1v1 apex duel, covert manipulation quote, or intriguing paradox ("That sweet compliment is a loaded weapon.", "Six lions backed down from one honey badger.", "Never ignore this four-second silence in conversation.").
       - No greetings, no textbook definitions, no passive "Did you know?" questions.

     * ACT 2 — NATURAL CONTEXT & CONCRETE REAL-LIFE PROOF (Scenes 2 – 3):
       - Smoothly build the context and show a concrete real-life example so the story never feels rushed or abrupt:
         * For Psychology / Manipulation / Relationships / Stoicism: Describe the real-life situation naturally and include a recognizable spoken quote ('They casually say, "You are just too sensitive," right after crossing your boundaries.').
         * For Mysteries / Deep Sea / History: Set the historical/scientific scene with the exact year, location, depth, or documented event.
         * For Wildlife / Animal Rescue: Build the tension of the encounter, exact physical traits, bite force (PSI), or the emotional turning point.

     * ACT 3 — DEEP MECHANISM EXPLANATION & MID-VIDEO TWIST (Scenes 4 – 5):
       - Explain *WHY* and *HOW* this happens thoroughly and clearly so the viewer truly understands the underlying mechanism (*The Isolation Gambit*, *Cognitive Dissonance*, *Cavitation Shockwave*, *Cortisol Loop*, or historical chain reaction).
       - Connect sentences smoothly with natural cause-and-effect transitions. Optionally set `overlay_badge` to a short uppercase tag like `"[ // THE REAL TRAP // ]"`.

     * ACT 4 — THE SURGICAL COUNTER-MOVE / CLIMAX PAYOFF (Scenes 6 – 7):
       - Deliver the full climax, resolution, or practical word-for-word Counter-Move that makes the video worth saving and sharing:
         * For Psychology: Give the exact calm response phrase and explain why it disarms the manipulator.
         * For Wildlife / Rescue / Mysteries: Deliver the complete biomechanical outcome, joyful transformation, or scientific answer that fully resolves the story.

     * ACT 5 — SATISFYING CONCLUSION & SEAMLESS INFINITE LOOP BRIDGE (Scene 8 or 9):
       - ZERO SPOKEN OUTRO CTA: NEVER say "Subscribe", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in the narration!
       - Conclude the story with a complete, resonant final thought that bridges naturally back into Scene 1's opening hook.

2. 1.8-SECOND SHOCK ANGLE HOOK (SCENE 1) & HIGH-CTR TITLE RULES:
   - The first 1.5 - 2.5 seconds determines 100% of Shorts performance (Viewed vs Swiped away).
   - HIGH-CTR TITLE MANDATE (LEARNED FROM CHANNEL ANALYTICS):
     * NEVER write bleak/depressing-only titles for Animal Rescue (FORBIDDEN: "Frozen Puppy Found in Rain", "Shivering Puppy Tied In Rain"). ALWAYS build a curiosity/transformation gap (WINNING FORMULA: "He Refused to Let Go Until This Happened 🥺❤️", "The Exact Moment He Realized He Was Finally Safe 🐾").
     * NEVER write vague generic titles for Wildlife (FORBIDDEN: "Invisible Monsters Waiting To Strike") or mix sports/off-niche words ("Bayern"). ALWAYS name the specific apex predators or duel ("Green Anaconda vs. Black Caiman: Brutal River Duel! 🐍🐊", "Why Lions REFUSE to Fight The Honey Badger 🦡🦁").
     * NEVER repeat saturated numbers like "11,000m" on Mysteries; use visceral second-person sensory/fear hooks ("Your Eyes Are Biologically Blind! 👁️", "Why Your Mind Snaps In Absolute Silence 🧠").
   - SCENE 1 HOOK FORMULAS (MUST CHOOSE 1 OF 4 CORE FORMULAS):
     * Formula 1 (Personal Pain Point & Hidden Vulnerability - HIGHEST RETENTION):
       Target personal insecurity, hidden exploitation, quiet anxiety, or counter-intuitive psychology using personal triggers ("You", "Your brain", "They"):
       - "3 subtle signs they are using you."
       - "Stop trying to be confident. Do this."
       - "If you feel drained around them, run."
       - "That sweet compliment is a loaded weapon."
       - "He refused to let go until this..."
     * Formula 2 (Apex Duel / Urgent Danger): "A 20-foot Anaconda ambushes a Caiman." / "If you see this symbol, run immediately."
     * Formula 3 (Pattern Interrupt / Elite Authority Secret): "This FBI trick exposes liars in seconds." / "The dark rule 99% learn too late."
     * Formula 4 (Dark Paradox / Counterfactual Twist): "What if Rome never fell, and ruled today?"
   - NEVER start with textbook definitions, greetings, or passive rhetorical questions ("Did you know...", "Welcome to...").
   - thumbnail_text MUST be 2 to 5 words in ALL CAPS with 1 high-contrast emoji (e.g. "THEY'RE USING YOU 🚩", "ANACONDA VS CAIMAN 🐍", "HE REFUSED TO LET GO 🥺", "ROME NEVER FELL? 🏛️").

3. SEAMLESS INFINITE LOOP ENDING (NO SPOKEN OUTRO CTA):
   - NEVER include "Subscribe", "Like", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in ANY scene's spoken narration!
   - Every single scene's spoken narration MUST be a 100% grammatically complete sentence with proper closing punctuation. NEVER end any scene with dangling prepositions or conjunctions ('because', 'which', 'and', 'so', 'that', 'but', 'with').

4. SIMPLE, NATURAL VISUAL KEYWORDS (`visual_keyword` & `stock_query` — RELATIVE MATCH IS FINE):
   - Do NOT overcomplicate per-scene keywords! Viewers care about a captivating, well-told story; visuals only need to fit the general theme, subject, or mood of the video.
   - Keep `visual_keyword` (2–5 English words) and `stock_query` (2–4 English words) focused on the main subject/entity and general atmosphere of the topic (e.g. `"honey badger fighting lion"`, `"man serious gaze dark noir"`, `"roman empire senate painting"`).
   - For psychology topics, use moody human expressions (`"man cold smirk dark room"`, `"woman thoughtful shadow noir"`) rather than literal props (`knife`, `cage`).

5. CINEMATIC ATMOSPHERE & LIGHTING:
   - Choose a cohesive visual mood for `visual_prompt` (e.g. Dark Psychological Noir for psychology, Warm Golden Hour or Documentary Realism for history/wildlife, Deep Bioluminescent Blue for ocean mysteries).

6. TITLE & LANGUAGE INTEGRITY (100% STRICT ENFORCEMENT):
   - The entire video script (spoken narration in all scenes, hook, full_narration, title) MUST be 100% in {target_lang_display}.
   - Clean, punchy title under 60 characters with emojis.

7. NATURAL MEDIA SELECTION (`media_type` PER SCENE):
   - Choose `'video'`, `'photo'`, `'documentary'`, or `'ai_image'` naturally based on the topic.

8. COHERENT, FULL-BODIED STORYTELLING (NEVER ABRUPT OR TRUNCATED):
   - Make sure every idea is fully developed and explained before moving to the next point.
   - Connect scenes with smooth narrative flow so the entire 85–120 word script reads like a seamless, gripping mini-documentary from start to finish.

9. HOOK VISUAL MODE DECISION (`hook_visual_mode`: `"video"` VS `"scene"`, `is_abstract_keyword`, & `climax_scene_id`):
   - Set `"hook_visual_mode": "video"` and `"is_abstract_keyword": false` when the topic involves concrete real-world footage (wildlife, animal rescues, natural phenomena).
   - Set `"hook_visual_mode": "scene"` and `"is_abstract_keyword": true` when the topic is psychological, abstract, or counterfactual ("What If").
   - Set `"climax_scene_id"` (integer between 2 and 7) and provide 2 simple English `"hook_search_queries"` (3–5 words each) related to the overall topic.

═══════════════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no markdown, no extra text:
═══════════════════════════════════════════════════════════
{{
  "title": "Viral YouTube Shorts Title with Emojis (28-55 chars, NO 'Variant' prefix)",
  "title_variants": [
    "Alternative High-CTR Curiosity Gap Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative Direct Warning / Numbered Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative Extreme Contrast / Counter-Move Title with Emoji (28-55 chars, NO 'Variant' prefix)"
  ],
  "primary_entity": "Full official name of main historical person/species or null (null for general psychology topics)",
  "hook": "The opening declarative hook sentence (5-9 words, NO passive questions)",
  "hook_variants": [
    "Declarative personal warning or pain-point hook sentence (5-9 words)",
    "Counter-intuitive psychological or physical paradox hook sentence (5-9 words)",
    "Immediate behavioral test or high-stakes action hook sentence (5-9 words)"
  ],
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "climax_scene_id": 3,
  "hook_search_queries": ["concrete short search query 1", "concrete short search query 2"],
  "full_narration": "Complete, smooth, well-developed spoken story without stage directions (85-120 words, 30-40s full 5-Act story with Real-Life Example, Mechanism, Counter-Move/Climax, and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-4 TỪ IN HOA CỰC TO GỢI TÒ MÒ CHO ẢNH BÌA",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Ý đồ dẫn dắt mạch truyện của cảnh này",
      "narration": "Complete, natural spoken sentence for this scene (Scene 1: 5-9 words; Body scenes: 8-16 words flowing naturally, 7 to 9 scenes total)",
      "visual_keyword": "simple relevant search query for the subject/theme (2-5 English words)",
      "visual_prompt": "Cinematic visual description fitting the overall story mood",
      "stock_query": "simple subject/mood query for stock video (2-4 English words)",
      "media_type": "video | ai_image | photo | documentary",
      "camera_motion": "zoom_in_fast | ken_burns_zoom_out | pan_right | ken_burns_zoom_in | pan_left",
      "sfx_cue": "impact_boom | whoosh_heavy | riser_tension | heartbeat_thump | glitch | reveal",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "[ // THE REAL TRAP // ] or null",
      "estimated_seconds": 4.0
    }}
  ],
  "tags": ["shorts", "viral"],
  "description": "Engaging description with hashtags",
  "pinned_comment": "Irresistible open-ended bait question in {target_lang_display} posted by channel owner to pin at top of comments",
  "viral_formula": "5-Act Full Storytelling | Smooth Transitions | Surgical Counter-Move | Seamless Loop"
}}
"""
        from app.services.content_linter import ScriptValidationLinter

        max_attempts = 3
        current_prompt = prompt
        last_script: Optional[ScriptContract] = None
        last_issues: List[str] = []

        for attempt in range(1, max_attempts + 1):
            try:
                raw_text = self._call_api(current_prompt, json_mode=False, timeout=30)
                script = self._parse_json_script(raw_text, channel_niche=channel_niche, topic=topic)
                last_script = script

                linter_res = ScriptValidationLinter.lint(
                    script=script,
                    niche_profile=niche_profile,
                    cooldown_entities=cooldown_entities,
                    target_wpm=niche_profile.get("target_wpm", 170)
                )

                if linter_res.is_valid:
                    logger.info(f"✅ [ContentAgent] Script passed Quality Gate Linter on attempt {attempt}/{max_attempts}")
                    return script

                last_issues = linter_res.issues
                logger.warning(
                    f"⚠️ [ContentAgent] Quality Gate failed (attempt {attempt}/{max_attempts}): "
                    f"{'; '.join(linter_res.issues)}. Retrying with corrective prompt..."
                )

                current_prompt = f"""{prompt}

═══════════════════════════════════════════════════════════
🚨 CORRECTION FEEDBACK FROM QUALITY LINTER (ATTEMPT {attempt}/{max_attempts}):
{linter_res.retry_prompt_hint}
PREVIOUS ATTEMPT FAILED ON:
{'; '.join(linter_res.issues)}
Rewrite immediately addressing ALL violations above!
═══════════════════════════════════════════════════════════
"""
            except Exception as e:
                logger.warning(f"[ContentAgent] Generation attempt {attempt} error: {e}")
                if "không nhận được phản hồi" in str(e) or "429" in str(e) or attempt == max_attempts:
                    if not last_script:
                        logger.info(
                            f"🛡️ [ContentAgent] Kích hoạt Curated 5-Act Viral Script Fallback cho '{topic}' ({channel_niche}, {language})"
                        )
                        return self._build_curated_5act_script_fallback(
                            topic=topic,
                            channel_niche=channel_niche,
                            language=language,
                        )

        logger.error(f"❌ [ContentAgent] Script failed Quality Gate Linter after {max_attempts} attempts: {last_issues}")
        if last_script:
            return last_script
        raise RuntimeError(f"Script validation failed after {max_attempts} attempts: {'; '.join(last_issues)}")

    def _build_curated_5act_script_fallback(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
    ) -> ScriptContract:
        """
        Bộ khung kịch bản 5 Hồi (8 phân cảnh, 95-115 từ) chuẩn Viral Shorts dự phòng
        khi toàn bộ Gemini API Keys tạm chạm hạn mức 429.
        Đảm bảo 100% đúng ngôn ngữ (pt-BR / en), đúng ngách (Biển sâu vs Trên cạn),
        và có từ khóa hình ảnh tiếng Anh cụ thể cho từng cảnh.
        """
        t_low = (topic or "").lower()
        n_low = (channel_niche or "").lower()
        is_pt = str(language or "").lower().startswith("pt")

        if is_pt and ("deep_sea" in n_low or "mystery" in n_low or "oceano" in t_low or "lula" in t_low or "nemo" in t_low or "tubarão" in t_low):
            if "nemo" in t_low or "cemitério" in t_low or "nave" in t_low:
                raw_data = {
                    "title": "Ponto Nemo: O Cemitério Secreto De Naves No Fundo Do Oceano 🌊🛰️ #Shorts",
                    "topic": topic,
                    "primary_entity": "Point Nemo",
                    "hook": "260 naves espaciais afundaram neste cemitério secreto do Pacífico!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["deep ocean abyss underwater", "spacecraft reentry ocean splash"],
                    "thumbnail_text": "CEMITÉRIO NO OCEANO! 🌊",
                    "pinned_comment": "Você teria coragem de navegar pelo Ponto Nemo à noite no meio do oceano? Comente! 👇",
                    "tags": ["shorts", "oceano", "misteriosdomundo", "pontonemo", "abismo"],
                    "scenes": [
                        {"scene_id": 1, "narration": "260 naves espaciais afundaram neste cemitério secreto do Pacífico!", "visual_keyword": "deep pacific ocean dark waves", "stock_query": "deep ocean waves", "visual_prompt": "Dark stormy Pacific Ocean waters from above", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Esse lugar se chama Ponto Nemo, o local mais isolado de todos os oceanos da Terra.", "visual_keyword": "remote ocean horizon aerial view", "stock_query": "remote ocean aerial", "visual_prompt": "Endless deep blue ocean far from any continent", "estimated_seconds": 4.5},
                        {"scene_id": 3, "narration": "Ele fica a 2.688 quilômetros da costa mais próxima, cercado apenas por águas profundas.", "visual_keyword": "underwater deep blue sea abyss", "stock_query": "underwater deep sea", "visual_prompt": "Deep underwater ocean rays fading into darkness", "estimated_seconds": 4.5},
                        {"scene_id": 4, "narration": "Quando um navio passa por lá, os humanos mais próximos estão na Estação Espacial Internacional.", "visual_keyword": "international space station orbit earth", "stock_query": "space station earth", "visual_prompt": "International Space Station orbiting above the Pacific Ocean", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Por isso as agências espaciais usam essa fossa oceânica para afundar satélites gigantes desativados.", "visual_keyword": "satellite burning atmosphere ocean", "stock_query": "satellite space orbit", "visual_prompt": "Spacecraft debris falling toward the ocean", "estimated_seconds": 4.8},
                        {"scene_id": 6, "narration": "Até a antiga estação soviética Mir de 120 toneladas repousa despedaçada no leito marinho escuro.", "visual_keyword": "sunken shipwreck deep ocean floor", "stock_query": "deep ocean shipwreck", "visual_prompt": "Metallic wreckage resting on the pitch black ocean floor", "estimated_seconds": 4.8},
                        {"scene_id": 7, "narration": "A pressão extrema e as correntes geladas bloqueiam quase toda vida marinha ao redor dos destroços.", "visual_keyword": "deep sea jellyfish bioluminescent dark", "stock_query": "deep sea creature", "visual_prompt": "Bioluminescent deep sea creature drifting in pitch black water", "estimated_seconds": 4.6},
                        {"scene_id": 8, "narration": "E é nesse silêncio absoluto do abismo marinho que o maior segredo do Pacífico permanece intocado.", "visual_keyword": "dark ocean trench underwater", "stock_query": "dark ocean underwater", "visual_prompt": "Deep ocean trench vanishing into darkness", "estimated_seconds": 4.5},
                    ],
                }
            else:
                raw_data = {
                    "title": "Lula-Colossal vs Cachalote: O Duelo Mortal A 2.000m No Oceano 🦑🐋 #Shorts",
                    "topic": topic,
                    "primary_entity": "Colossal Squid",
                    "hook": "Lula-colossal contra cachalote: o duelo mais brutal do oceano!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["sperm whale diving deep ocean", "giant squid underwater ocean"],
                    "thumbnail_text": "DUELO A 2.000M NO MAR! 🦑",
                    "pinned_comment": "Quem vence na escuridão total do oceano profundo: Cachalote ou Lula-Colossal? Comente! 👇",
                    "tags": ["shorts", "oceano", "misteriosdomundo", "baleia", "abismo"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Lula-colossal contra cachalote: o duelo mais brutal do oceano!", "visual_keyword": "sperm whale diving deep ocean", "stock_query": "sperm whale underwater", "visual_prompt": "Massive sperm whale diving into the dark ocean abyss", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Essas marcas circulares na pele da baleia vêm dos tentáculos da temida lula-colossal.", "visual_keyword": "giant squid tentacles underwater dark", "stock_query": "squid underwater ocean", "visual_prompt": "Giant squid tentacles moving through dark ocean water", "estimated_seconds": 4.5},
                        {"scene_id": 3, "narration": "Com 14 metros e olhos do tamanho de pratos, ela caça na escuridão congelante da Antártida.", "visual_keyword": "deep sea creature glowing eyes", "stock_query": "deep ocean creature", "visual_prompt": "Huge squid eye gleaming in the pitch black Southern Ocean", "estimated_seconds": 4.6},
                        {"scene_id": 4, "narration": "Diferente da lula comum, suas ventosas possuem ganchos afiados que giram 360 graus na carne.", "visual_keyword": "octopus tentacles closeup underwater", "stock_query": "tentacles underwater closeup", "visual_prompt": "Powerful cephalopod tentacles gripping in deep water", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Mas o cachalote de 50 toneladas mergulha com a maior arma acústica de todos os oceanos.", "visual_keyword": "whale swimming deep blue water", "stock_query": "whale deep ocean", "visual_prompt": "Giant whale descending into the deep blue trench", "estimated_seconds": 4.6},
                        {"scene_id": 6, "narration": "Ele dispara cliques de sonar de 230 decibéis que funcionam como uma onda de choque submarina.", "visual_keyword": "underwater sonar sound wave ocean", "stock_query": "underwater ocean rays", "visual_prompt": "Acoustic shockwave rippling through dark underwater depths", "estimated_seconds": 4.8},
                        {"scene_id": 7, "narration": "O impacto sonoro desorienta a lula-colossal no abismo antes do golpe final das mandíbulas.", "visual_keyword": "predator whale hunting deep sea", "stock_query": "whale hunting ocean", "visual_prompt": "Sperm whale emerging victorious from the deep sea", "estimated_seconds": 4.6},
                        {"scene_id": 8, "narration": "Ainda assim, nas fossas mais profundas do oceano, essa guerra invisível acontece todos os dias.", "visual_keyword": "dark ocean abyssal trench", "stock_query": "dark ocean abyss", "visual_prompt": "Endless dark ocean abyss at 2000 meters depth", "estimated_seconds": 4.4},
                    ],
                }
        elif "history" in n_low or "what_if" in n_low:
            raw_data = {
                "title": "One Man Refused The Order To End The World ☢️🚢 #Shorts",
                "topic": topic,
                "primary_entity": "Vasili Arkhipov",
                "hook": "One Soviet officer refused to launch World War Three!",
                "hook_visual_mode": "video",
                "hook_search_queries": ["submarine underwater ocean", "naval warship sea storm"],
                "thumbnail_text": "1 VOTE SAVED EARTH! ☢️",
                "pinned_comment": "If Vasili Arkhipov had said YES on submarine B-59 that day, would civilization still exist? 👇",
                "tags": ["shorts", "alternatehistory", "whatif", "history", "coldwar"],
                "scenes": [
                    {"scene_id": 1, "narration": "One Soviet officer refused to launch World War Three!", "visual_keyword": "submarine underwater dark ocean", "stock_query": "submarine underwater", "visual_prompt": "Soviet diesel submarine submerged in dark Atlantic waters", "estimated_seconds": 4.3},
                    {"scene_id": 2, "narration": "American destroyers had cornered Soviet submarine B-59 near Cuba and begun dropping signaling depth charges.", "visual_keyword": "navy destroyer warship ocean waves", "stock_query": "navy warship ocean", "visual_prompt": "US Navy destroyers cutting through rough ocean waves", "estimated_seconds": 4.8},
                    {"scene_id": 3, "narration": "Cut off from Moscow for days in 120-degree heat, the exhausted crew believed World War Three had started.", "visual_keyword": "submarine control room gauges red light", "stock_query": "submarine interior red", "visual_prompt": "Tense submarine control room bathed in red emergency light", "estimated_seconds": 4.8},
                    {"scene_id": 4, "narration": "Inside the hull sat a 10-kiloton nuclear torpedo capable of vaporizing the entire American carrier fleet.", "visual_keyword": "torpedo tube submarine weapon", "stock_query": "military torpedo", "visual_prompt": "Heavy torpedo loaded inside a submarine launch tube", "estimated_seconds": 4.6},
                    {"scene_id": 5, "narration": "Captain Savitsky and the political officer both signed off to launch the nuclear strike immediately.", "visual_keyword": "military officer radar screen", "stock_query": "radar screen military", "visual_prompt": "Naval officers staring at sonar and radar consoles", "estimated_seconds": 4.5},
                    {"scene_id": 6, "narration": "What if their order had been enough? Washington and Moscow would have been glassed within ninety minutes.", "visual_keyword": "nuclear explosion mushroom cloud", "stock_query": "explosion cloud", "visual_prompt": "Massive nuclear mushroom cloud rising over the horizon", "estimated_seconds": 4.8},
                    {"scene_id": 7, "narration": "Instead, flotilla commander Vasili Arkhipov stood his ground in the stifling control room and vetoed the launch.", "visual_keyword": "naval commander portrait dramatic light", "stock_query": "navy captain", "visual_prompt": "Determined Soviet naval officer refusing the launch key", "estimated_seconds": 4.8},
                    {"scene_id": 8, "narration": "That single stubborn refusal forced B-59 to surface and stopped the deadliest torpedo in history.", "visual_keyword": "submarine surfacing ocean sunset", "stock_query": "submarine ocean", "visual_prompt": "Submarine surfacing peacefully on the ocean waves", "estimated_seconds": 4.5},
                ],
            }
        elif "psychology" in n_low:
            raw_data = {
                "title": "Why FBI Agents Watch Your FEET When You Lie 🕵️‍♂️🧠 #Shorts",
                "topic": topic,
                "primary_entity": "Joe Navarro",
                "hook": "Trained liars freeze their face, but their feet confess!",
                "hook_visual_mode": "video",
                "hook_search_queries": ["detective interrogation room", "person tapping feet nervous"],
                "thumbnail_text": "WATCH THEIR FEET! 🕵️‍♂️",
                "pinned_comment": "Have you ever caught someone lying by watching their body language instead of their words? 👇",
                "tags": ["shorts", "psychology", "darkpsychology", "bodylanguage", "fbi"],
                "scenes": [
                    {"scene_id": 1, "narration": "Trained liars freeze their face, but their feet confess!", "visual_keyword": "interrogation room detective table", "stock_query": "interrogation detective", "visual_prompt": "Suspect sitting across a metal table in a dim interrogation room", "estimated_seconds": 4.3},
                    {"scene_id": 2, "narration": "When someone rehearses a lie, they spend all their mental energy managing their eyes and fake smile.", "visual_keyword": "person closeup eyes intense stare", "stock_query": "intense eyes closeup", "visual_prompt": "Extreme closeup of nervous eyes trying to stay calm", "estimated_seconds": 4.6},
                    {"scene_id": 3, "narration": "Former FBI counterintelligence agent Joe Navarro discovered the truth leaks out at the bottom of the table.", "visual_keyword": "fbi agent reviewing case files", "stock_query": "detective investigating", "visual_prompt": "Behavioral analyst observing a suspect through glass", "estimated_seconds": 4.8},
                    {"scene_id": 4, "narration": "Your feet are wired directly to the brain's ancient limbic system, which reacts before conscious thought.", "visual_keyword": "human brain neural network glowing", "stock_query": "brain neural network", "visual_prompt": "Glowing limbic system firing signals inside the human brain", "estimated_seconds": 4.7},
                    {"scene_id": 5, "narration": "The instant a deceptive person feels trapped by a question, their feet unconsciously pivot toward the nearest exit.", "visual_keyword": "shoes feet pointing toward door", "stock_query": "walking shoes floor", "visual_prompt": "Close shot of dress shoes pivoting toward an exit door", "estimated_seconds": 4.9},
                    {"scene_id": 6, "narration": "Even more telling, if their ankles suddenly lock tightly around the chair legs, their fight-or-flight freeze reflex just triggered.", "visual_keyword": "nervous person sitting chair legs", "stock_query": "person sitting nervous", "visual_prompt": "Feet locking around chair legs under an office table", "estimated_seconds": 5.0},
                    {"scene_id": 7, "narration": "Next time you suspect a lie, drop a tough question and glance down at their shoes within two seconds.", "visual_keyword": "businessman conversation office", "stock_query": "office conversation", "visual_prompt": "Sharp observer noticing subtle body language cues", "estimated_seconds": 4.7},
                    {"scene_id": 8, "narration": "Because while the mouth manufactures an alibi, the feet are already trying to escape the room.", "visual_keyword": "person walking away dark corridor", "stock_query": "walking dark corridor", "visual_prompt": "Footsteps retreating down a dimly lit hallway", "estimated_seconds": 4.4},
                ],
            }
        elif "deep_sea" in n_low or "mystery" in n_low:
            raw_data = {
                "title": "The Secret Spacecraft Graveyard At The Bottom Of The Ocean 🌊🛰️ #Shorts",
                "topic": topic,
                "primary_entity": "Point Nemo",
                "hook": "260 sunken spacecraft rest in this secret ocean graveyard!",
                "hook_visual_mode": "video",
                "hook_search_queries": ["deep ocean underwater abyss", "satellite space orbit earth"],
                "thumbnail_text": "OCEAN SPACE GRAVEYARD! 🌊",
                "pinned_comment": "Would you dare sail across Point Nemo at night knowing you are 1,670 miles from land? 👇",
                "tags": ["shorts", "deepsea", "oceanmysteries", "pointnemo", "science"],
                "scenes": [
                    {"scene_id": 1, "narration": "260 sunken spacecraft rest in this secret ocean graveyard!", "visual_keyword": "deep pacific ocean dark waves", "stock_query": "deep ocean waves", "visual_prompt": "Dark swells of the South Pacific Ocean", "estimated_seconds": 4.3},
                    {"scene_id": 2, "narration": "Scientists call this coordinate Point Nemo, the oceanic pole of inaccessibility.", "visual_keyword": "remote ocean horizon aerial view", "stock_query": "remote ocean aerial", "visual_prompt": "Aerial view of endless deep blue ocean water", "estimated_seconds": 4.4},
                    {"scene_id": 3, "narration": "It sits 1,670 miles from the nearest strip of land in every single direction.", "visual_keyword": "underwater deep blue sea abyss", "stock_query": "underwater deep sea", "visual_prompt": "Sunlight fading into the deep ocean abyss", "estimated_seconds": 4.4},
                    {"scene_id": 4, "narration": "When you stand at Point Nemo, the closest human beings to you are astronauts orbiting 250 miles overhead.", "visual_keyword": "international space station orbit earth", "stock_query": "space station earth", "visual_prompt": "International Space Station orbiting above Earth's oceans", "estimated_seconds": 4.9},
                    {"scene_id": 5, "narration": "Because no ships sail there, space agencies deliberately crash dying satellites straight into this abyssal trench.", "visual_keyword": "satellite burning atmosphere ocean", "stock_query": "satellite space orbit", "visual_prompt": "Fiery spacecraft debris streaking over the ocean", "estimated_seconds": 4.8},
                    {"scene_id": 6, "narration": "Even the 120-ton Russian Mir space station rests shattered across the freezing seafloor.", "visual_keyword": "sunken shipwreck deep ocean floor", "stock_query": "deep ocean shipwreck", "visual_prompt": "Sunken metallic wreckage on the dark ocean floor", "estimated_seconds": 4.6},
                    {"scene_id": 7, "narration": "Crushing pressure and nutrient-starved currents leave the surrounding water almost completely devoid of fish.", "visual_keyword": "deep sea jellyfish bioluminescent dark", "stock_query": "deep sea creature", "visual_prompt": "Lonely bioluminescent jellyfish drifting in pitch black water", "estimated_seconds": 4.7},
                    {"scene_id": 8, "narration": "And in that pitch-black oceanic silence, humanity's largest space machines slowly rust in the abyss.", "visual_keyword": "dark ocean trench underwater", "stock_query": "dark ocean underwater", "visual_prompt": "Pitch black abyssal seafloor at 13,000 feet", "estimated_seconds": 4.5},
                ],
            }
        else:
            raw_data = {
                "title": "How One Bite From A Komodo Dragon Drops A 1,200lb Giant 🦎💀 #Shorts",
                "topic": topic,
                "primary_entity": "Komodo Dragon",
                "hook": "One Komodo dragon bite drops a 1,200-pound buffalo!",
                "hook_visual_mode": "video",
                "hook_search_queries": ["komodo dragon walking wild", "water buffalo horns wildlife"],
                "thumbnail_text": "1 BITE DROPS A GIANT! 🦎",
                "pinned_comment": "Could any land predator survive a venomous bite from a giant Komodo Dragon? Drop your vote 👇",
                "tags": ["shorts", "wildlife", "predators", "komododragon", "nature"],
                "scenes": [
                    {"scene_id": 1, "narration": "One Komodo dragon bite drops a 1,200-pound buffalo!", "visual_keyword": "komodo dragon walking wild nature", "stock_query": "komodo dragon", "visual_prompt": "Giant Komodo dragon stalking across dry island terrain", "estimated_seconds": 4.3},
                    {"scene_id": 2, "narration": "For decades, people believed dirty saliva killed the dragon's prey, but CT scans revealed a deadlier weapon.", "visual_keyword": "komodo dragon closeup tongue", "stock_query": "komodo dragon closeup", "visual_prompt": "Extreme closeup of a Komodo dragon flicking its yellow forked tongue", "estimated_seconds": 4.8},
                    {"scene_id": 3, "narration": "Inside its lower jaw sit sixty curved, shark-like teeth coated in a natural layer of pure iron.", "visual_keyword": "reptile sharp teeth jaw closeup", "stock_query": "reptile closeup", "visual_prompt": "Serrated reptile teeth gleaming in sunlight", "estimated_seconds": 4.7},
                    {"scene_id": 4, "narration": "Instead of crushing bone like a crocodile, the Komodo dragon clamps onto the buffalo's leg and pulls backward.", "visual_keyword": "water buffalo standing mud wild", "stock_query": "water buffalo wild", "visual_prompt": "Massive water buffalo standing defensively in muddy grass", "estimated_seconds": 4.9},
                    {"scene_id": 5, "narration": "As those serrated teeth slice deep grooves, twin venom glands along its jaw pump potent anticoagulants into the wound.", "visual_keyword": "komodo dragon hunting prey", "stock_query": "lizard hunting", "visual_prompt": "Komodo dragon lunging forward with jaws wide open", "estimated_seconds": 5.0},
                    {"scene_id": 6, "narration": "The venom blocks blood clotting completely and sends the 1,200-pound giant into rapid hypotensive shock.", "visual_keyword": "buffalo herd running savanna", "stock_query": "buffalo running", "visual_prompt": "Water buffalo stumbling through shallow water", "estimated_seconds": 4.7},
                    {"scene_id": 7, "narration": "The dragon doesn't even need to chase; it simply follows the scent trail with its forked tongue for hours.", "visual_keyword": "komodo dragon walking forest", "stock_query": "giant lizard walking", "visual_prompt": "Komodo dragon tracking prey patiently across the island", "estimated_seconds": 4.8},
                    {"scene_id": 8, "narration": "And when the exhausted giant finally collapses, the patient apex predator claims its 1,000-pound prize.", "visual_keyword": "komodo dragon apex predator island", "stock_query": "komodo dragon nature", "visual_prompt": "Komodo dragon standing dominant on the island shore", "estimated_seconds": 4.5},
                ],
            }

        raw_data["full_narration"] = " ".join(s["narration"] for s in raw_data["scenes"])
        return self._parse_json_script(json.dumps(raw_data, ensure_ascii=False), channel_niche=channel_niche, topic=topic)

    def refine_script(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
    ) -> ScriptContract:
        """
        Tiếp thu góp ý của người dùng/Critic để tinh chỉnh phẫu thuật (Surgical Refinement) kịch bản hiện tại chuẩn viral.
        """
        lang_instruction = build_language_integrity_instruction(language)
        lang_clean = str(language or "en").strip().lower()
        if lang_clean.startswith("vi"):
            target_lang_display = "TIẾNG VIỆT"
        elif lang_clean.startswith("pt"):
            target_lang_display = "PORTUGUÊS BRASILEIRO"
        else:
            target_lang_display = "ENGLISH"

        director_block = ""
        if director_notes and len(director_notes.strip()) > 3:
            director_block = f"\nCREATOR DIRECTOR NOTES (MUST PRESERVE):\n\"{director_notes.strip()}\"\n"

        prompt = f"""
You are an elite viral YouTube Shorts Master Showrunner and Behavioral Storyteller.
We have an existing script, and our Quality Critic / Creator provided specific surgical revisions:

CRITIQUE & REVISION FEEDBACK:
"{feedback}"
{director_block}
CURRENT SCRIPT:
{current_script.model_dump_json(indent=2)}

SURGICALLY REFINE the script strictly addressing the feedback above while preserving the strengths, core entity, and narrative soul of the existing script.
Target Language: {language} ({target_lang_display})
{lang_instruction}

HARD RETENTION & FULL-BODIED 5-ACT STORYTELLING RULES:
1. SCENE 1 PATTERN INTERRUPT HOOK (CRITICAL TO PREVENT SWIPING):
   - Scene 1 spoken text must use an urgent warning, 1v1 apex duel, elite interrogation tactic, dark law, or counter-intuitive shock (5-9 words).
2. SMOOTH, COMPLETE 5-ACT NARRATIVE (85-120 words total across 7-9 scenes, flexible 8-16 words per body scene — NEVER write choppy or truncated sentences!):
   - Act 1 (Scene 1): Hook (5-9 words).
   - Act 2 (Scenes 2-3): Smooth context + concrete real-life verbal quote/example or specific historical/scientific proof.
   - Act 3 (Scenes 4-5): Thorough explanation of the hidden neurological/psychological/biomechanical mechanism + Mid-Video Twist.
   - Act 4 (Scenes 6-7): Complete climax or concrete word-for-word Counter-Move that viewers will Save & Share.
   - Act 5 (Scene 8-9): Satisfying conclusion + Seamless Infinite Loop Bridge back to Scene 1 (ZERO spoken Subscribe CTA).
3. SIMPLE, NATURAL `visual_keyword` & `stock_query` (RELATIVE MATCH IS FINE):
   - Keep `visual_keyword` (2-5 English words) and `stock_query` (2-4 English words) simple and relevant to the general subject/mood of the story.
4. Return ONLY valid JSON:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "primary_entity": "Full official name of main person/figure or null",
  "hook": "The opening hook sentence (5-9 words)",
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "climax_scene_id": 3,
  "hook_search_queries": ["concrete short search query 1", "concrete short search query 2"],
  "full_narration": "The full, smooth, well-developed spoken story without stage directions (85-120 words, 30-40s full 5-Act story with Real-Life Example, Mechanism, Counter-Move, and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-4 TỪ IN HOA CỰC TO GỢI TÒ MÒ CHO ẢNH BÌA",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Ý đồ dẫn dắt mạch truyện của cảnh này",
      "narration": "Complete, natural sentence spoken in this scene (Scene 1: 5-9 words; Body scenes: 8-16 words flowing naturally, 7-9 scenes total)",
      "visual_keyword": "simple relevant search query for the subject/theme (2-5 English words)",
      "visual_prompt": "Cinematic visual description fitting the overall story mood",
      "stock_query": "simple subject/mood query for stock video (2-4 English words)",
      "media_type": "video | ai_image | photo | documentary",
      "camera_motion": "zoom_in_fast | ken_burns_zoom_out | pan_right | ken_burns_zoom_in | pan_left",
      "sfx_cue": "impact_boom | whoosh_heavy | riser_tension | heartbeat_thump | glitch | reveal",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "[ // THE REAL TRAP // ] or null",
      "estimated_seconds": 4.0
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags",
  "pinned_comment": "Provocative engagement bait question for pinned comment in {target_lang_display}",
  "viral_formula": "Seamless Infinite Loop | Micro-Hook Escalation | Pattern Interrupt"
}}
"""
        raw_text = self._call_api(prompt, json_mode=False, timeout=30)
        return self._parse_json_script(
            raw_text,
            channel_niche=channel_niche,
            topic=getattr(current_script, "topic", "") or getattr(current_script, "title", ""),
        )

    def chat_brainstorm(
        self,
        session_id: str,
        user_message: str,
        channel_niche: str = "general",
        language: str = "en"
    ) -> str:
        """
        Thảo luận, phản biện và đồng sáng tạo ý tưởng Shorts với người dùng.
        Lưu lịch sử hội thoại cho session_id và tự động xoay key nếu quá tải.
        """
        if session_id not in self.conversations:
            self.conversations[session_id] = []

        history = self.conversations[session_id]

        system_instruction = f"""
Bạn là Giám đốc Sáng tạo & Đạo diễn YouTube Shorts triệu view hàng đầu thế giới (kênh ngách: '{channel_niche}').
Nhiệm vụ của bạn là đồng hành, tư duy cùng người dùng (và nhóm sáng tạo) để thảo luận, phản biện, tìm góc nhìn viral độc lạ và hoàn thiện kịch bản Shorts.

Phong cách làm việc:
1. Trả lời bằng ngôn ngữ: {language} (ngắn gọn, súc tích, truyền cảm hứng, đi thẳng vào bản chất).
2. Luôn chú trọng:
   - HOOK 3 giây đầu (yếu tố sống còn giữ chân người xem).
   - Nhịp điệu kịch tính (Pacing), góc nhìn gây tranh cãi hoặc bất ngờ (Pattern Interrupt).
   - Kêu gọi tương tác (Call to Action / Open Loop) để người xem bình luận sôi nổi.
3. Khi người dùng đưa ra một chủ đề hoặc câu hỏi, hãy:
   - Phân tích ngắn gọn vì sao chủ đề này ăn view hoặc điểm yếu cần khắc phục.
   - Đưa ra 2-3 gợi ý Hook giật gân khác nhau.
   - Đề xuất cốt truyện 30-45 giây súc tích.
4. Trả lời dưới dạng định dạng text rõ ràng, có emoji sinh động, không quá dài dòng (dưới 350 từ để đọc dễ trên Telegram).
"""
        recent_history = history[-10:]
        formatted_dialogue = []
        for turn in recent_history:
            role_label = "Creator" if turn["role"] == "user" else "Director"
            formatted_dialogue.append(f"{role_label}: {turn['content']}")
        formatted_dialogue.append(f"Creator: {user_message}")

        full_prompt = f"{system_instruction}\n\nLỊCH SỬ THẢO LUẬN:\n" + "\n".join(formatted_dialogue) + "\n\nDirector:"

        try:
            reply_text = self._call_api(full_prompt, json_mode=False, timeout=20)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "model", "content": reply_text})
            if len(history) > 20:
                self.conversations[session_id] = history[-20:]
            return reply_text
        except Exception as e:
            logger.warning(f"Lỗi khi chat Gemini: {e}")
            err_str = str(e)
            if "quota" in err_str.lower() or "429" in err_str:
                return "⚠️ Các Gemini API Key trong pool tạm thời đạt giới hạn request/phút. Vui lòng thử lại sau 15-30 giây!"
            return f"⚠️ Lỗi kết nối AI: {err_str[:120]}. Vui lòng thử lại!"

    def convert_discussion_to_script(
        self,
        session_id: str,
        channel_niche: str = "general",
        language: str = "en"
    ) -> ScriptContract:
        """
        Chuyển đổi toàn bộ tinh hoa từ đoạn chat thảo luận thành một kịch bản phân cảnh chuẩn ScriptContract.
        """
        history = self.conversations.get(session_id, [])
        if not history:
            return self.generate_script(topic="Viral Shorts Concept", channel_niche=channel_niche, language=language)

        dialogue_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-8:]])

        lang_instruction = build_language_integrity_instruction(language)
        target_lang_display = "TIẾNG VIỆT" if str(language).lower().startswith("vi") else "ENGLISH"

        prompt = f"""
You are an expert YouTube Shorts producer and cinematographer.
Based on the following brainstorm discussion between a creator and director:
---
{dialogue_text}
---

Synthesize this entire discussion into a complete, high-retention 40-50 second YouTube Shorts script.
Niche: '{channel_niche}'
Target Language: {language} ({target_lang_display})
{lang_instruction}

HARD RULES:
1. SCENE 1 HOOK (PREVENT SWIPING): Choose an electric 5-8 word pattern-interrupt hook (Warning, Elite Move, Dark Law, or Counter-Intuitive Shock). Never passive or textbook.
2. Fast-paced, punchy, concise sentences.
3. visual_prompt must depict the ACTUAL PHENOMENON, never the literal meaning of a metaphor:
   - narration: "like a mountain crushed to sand" → visual_prompt: stellar collapse, NOT a mountain
   - Each scene: visually UNIQUE subject, angle, and lighting from all others.
4. visual_prompt format: "[Subject + action], [camera angle], [lighting], [environment detail]"
5. stock_query: 2-3 English topic words for Pexels ONLY. FORBIDDEN: movie titles, actor names, director names.
   BAD: "Interstellar Nolan voyage" | GOOD: "black hole space" or "galaxy explosion"
6. Return ONLY valid JSON:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Spoken text in this scene",
      "visual_keyword": "black hole event horizon",
      "visual_prompt": "Detailed cinematic scene: subject + camera angle + lighting + environment",
      "stock_query": "black hole",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags"
}}
"""
        raw_text = self._call_api(prompt, json_mode=True, timeout=30)
        return self._parse_json_script(raw_text, channel_niche=channel_niche)

    def generate_best_script(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        min_acceptable_score: float = 8.0,
        max_attempts: int = 3,
        cooldown_entities: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
        strict_block: bool = True,
        **kwargs,
    ) -> tuple:
        """
        Quy trình biên soạn cao cấp: Tạo kịch bản -> Tự động chấm điểm bằng ScriptCritic
        với ngưỡng Hook được hiệu chỉnh theo tỷ lệ vuốt bỏ thật (swipe_away_pct) của kênh.
        Nếu sau max_attempts vẫn không đạt ngưỡng và strict_block=True -> Chặn cứng (raise RuntimeError).
        Trả về tuple: (ScriptContract, ScriptEvaluationReport).
        """
        from app.graph.evaluators.script_critic import ScriptCritic
        critic = ScriptCritic()
        cal_thresh = critic.get_calibrated_thresholds(channel_id=channel_id, channel_style=channel_niche)
        effective_min_overall = max(float(min_acceptable_score or 8.0), float(cal_thresh.get("min_overall_score", 8.0)))
        effective_min_hook = float(cal_thresh.get("min_hook_score", 8.0))
        avg_swipe = cal_thresh.get("avg_swipe_away_pct")

        # 1. Tạo kịch bản sơ khởi
        best_script = self.generate_script(
            topic=topic,
            channel_niche=channel_niche,
            language=language,
            director_notes=director_notes,
            cooldown_entities=cooldown_entities,
            channel_id=channel_id,
            **kwargs,
        )

        try:
            best_eval = critic.evaluate_script(
                script_data=best_script.model_dump(),
                topic=topic,
                channel_style=channel_niche,
                primary_entity=getattr(best_script, "primary_entity", None),
                channel_id=channel_id,
            )
        except Exception as e:
            logger.warning(f"Lỗi khi chấm điểm kịch bản sơ bộ: {e}")
            if strict_block:
                raise RuntimeError(f"🚫 [HardBlock QA] Không thể thẩm định chất lượng kịch bản: {e}")
            return best_script, None

        # Nếu ngay vòng 1 đã đạt chuẩn xuất sắc (cả overall lẫn hook_strength) -> Trả về ngay
        if (
            best_eval.overall_score >= effective_min_overall
            and best_eval.hook_strength.score >= effective_min_hook
            and best_eval.passed
        ):
            logger.info(
                f"✅ [ContentAgent] Kịch bản đạt chuẩn ngay vòng 1: "
                f"Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f} | "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}"
            )
            return best_script, best_eval

        # 2. Vòng lặp tự động tái tạo & hoàn thiện cho đến khi đạt điểm chuẩn
        logger.info(
            f"⚠️ [ContentAgent] Kịch bản sơ bộ chưa đạt "
            f"(Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
            f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
            f"Channel Avg Swipe-Away={avg_swipe}%). "
            f"Bắt đầu quy trình tự động tái tạo (Tối đa {max_attempts} lần thử)..."
        )

        lang_low = str(language or "en").lower()
        if lang_low.startswith("vi"):
            target_lang_note = "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
        elif lang_low.startswith("pt"):
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% BRAZILIAN PORTUGUESE (PORTUGUÊS DO BRASIL). All rewritten narration, hook, and title MUST be in natural Brazilian Portuguese."
        else:
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."

        for attempt in range(1, max_attempts + 1):
            critiques_list = list(best_eval.actionable_critiques) if best_eval.actionable_critiques else []

            # Thêm chỉ đạo chuyên sâu theo các tiêu chí bị điểm kém (đặc biệt là ngưỡng Hook theo swipe_away_pct)
            if best_eval.hook_strength.score < effective_min_hook:
                critiques_list.insert(
                    0,
                    f"Cảnh 1 Hook chưa đạt ngưỡng chống vuốt bỏ ({best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
                    f"kênh có swipe_away={avg_swipe}%): {best_eval.hook_strength.feedback}. "
                    f"BẮT BUỘC dùng công thức pt-BR / Wild Odyssey: cấu trúc đối đầu 'A vs B' + câu khẳng định gây sốc 5-8 từ có con số/thông số vật lý cụ thể ngay giây 0.0s."
                )
            if best_eval.story_pacing.score < 8.0:
                critiques_list.append(f"Nhịp điệu chưa chuẩn ({best_eval.story_pacing.score:.1f}/10): {best_eval.story_pacing.feedback}. Điều chỉnh lời thoại strictly 88-118 từ cho toàn bộ video, 3.5-4.5s/cảnh, 8-9 cảnh.")
            if getattr(best_eval, "visual_direction", None) and best_eval.visual_direction.score < 8.0:
                critiques_list.append(f"Thị giác yếu ({best_eval.visual_direction.score:.1f}/10): Cấm từ ngữ trừu tượng. visual_keyword và stock_query phải là danh từ/hành động vật lý cụ thể.")

            directive = (
                f"KỊCH BẢN TRƯỚC ĐÓ ĐẠT ĐIỂM KÉM (Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}). {target_lang_note} "
                f"CHỈ ĐẠO BẮT BUỘC SỬA ĐỂ ĐẠT CHUẨN VIRAL: " + " | ".join(critiques_list)
            )

            try:
                if attempt < max_attempts:
                    # Tinh chỉnh sâu kịch bản hiện tại với các chỉ đạo sửa lỗi
                    candidate_script = self.refine_script(
                        current_script=best_script,
                        feedback=directive,
                        channel_niche=channel_niche,
                        language=language,
                        director_notes=director_notes,
                    )
                else:
                    # Lần thử cuối cùng: Tái tạo kịch bản từ đầu với góc nhìn sốc và sắc bén hơn
                    refreshed_topic = f"{topic} (A vs B confrontation, shocking physical claim hook)"
                    merged_notes = f"{director_notes or ''}\nMUST FIX: {'; '.join(critiques_list[:3])}".strip()
                    candidate_script = self.generate_script(
                        topic=refreshed_topic,
                        channel_niche=channel_niche,
                        language=language,
                        director_notes=merged_notes,
                        cooldown_entities=cooldown_entities,
                        channel_id=channel_id,
                        **kwargs,
                    )

                candidate_eval = critic.evaluate_script(
                    script_data=candidate_script.model_dump(),
                    topic=topic,
                    channel_style=channel_niche,
                    primary_entity=getattr(candidate_script, "primary_entity", None),
                    channel_id=channel_id,
                )

                logger.info(
                    f"🔄 [ContentAgent Auto-Retry {attempt}/{max_attempts}] "
                    f"Overall: {candidate_eval.overall_score:.1f}/{effective_min_overall:.1f} | "
                    f"Hook: {candidate_eval.hook_strength.score:.1f}/{effective_min_hook:.1f} | "
                    f"Passed: {candidate_eval.passed}"
                )

                # Cập nhật ứng viên tốt nhất (ưu tiên kịch bản passed hoặc có tổng điểm + hook cao hơn)
                cand_rank = (1 if candidate_eval.passed else 0, candidate_eval.hook_strength.score * 0.5 + candidate_eval.overall_score * 0.5)
                best_rank = (1 if best_eval.passed else 0, best_eval.hook_strength.score * 0.5 + best_eval.overall_score * 0.5)
                if cand_rank > best_rank:
                    best_script = candidate_script
                    best_eval = candidate_eval

                # Kiểm tra nếu đã vượt qua ngưỡng chuẩn
                if (
                    candidate_eval.overall_score >= effective_min_overall
                    and candidate_eval.hook_strength.score >= effective_min_hook
                    and candidate_eval.passed
                ):
                    logger.info(
                        f"🎉 [ContentAgent] Kịch bản đã đạt chuẩn xuất sắc "
                        f"(Overall={candidate_eval.overall_score:.1f}, Hook={candidate_eval.hook_strength.score:.1f}) ở lần thử {attempt}!"
                    )
                    return candidate_script, candidate_eval

            except Exception as e:
                logger.warning(f"Lỗi trong vòng lặp tự sửa kịch bản lần {attempt}: {e}")
                continue

        if strict_block and (
            not best_eval.passed
            or best_eval.overall_score < effective_min_overall
            or best_eval.hook_strength.score < effective_min_hook
        ):
            raise RuntimeError(
                f"🚫 [HardBlock ScriptQA] Kịch bản/Hook không đạt ngưỡng giữ chân sau {max_attempts} vòng tối ưu "
                f"(Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
                f"Channel Swipe-Away={avg_swipe}%). Đã chặn sản xuất video không đạt chuẩn!"
            )

        logger.info(f"🏁 [ContentAgent] Hoàn thành tối ưu hóa kịch bản, điểm tốt nhất đạt được: {best_eval.overall_score:.1f}/10")
        return best_script, best_eval

    def refine_with_feedback_and_eval(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en",
        min_acceptable_score: float = 8.0,
        max_attempts: int = 3
    ) -> tuple:
        """
        Tiếp thu góp ý chỉnh sửa của người dùng, tổng hợp lại kịch bản,
        tự động chấm điểm qua ScriptCritic và tinh chỉnh liên tục cho đến khi đạt chuẩn.
        Trả về tuple: (ScriptContract, ScriptEvaluationReport).
        """
        from app.graph.evaluators.script_critic import ScriptCritic
        critic = ScriptCritic()

        logger.info(f"✍️ [ContentAgent] Bắt đầu tổng hợp góp ý: '{feedback}' cho kênh {channel_niche} ({language})")

        # 1. Tinh chỉnh lần 1 dựa trên góp ý trực tiếp của người dùng
        best_script = self.refine_script(
            current_script=current_script,
            feedback=feedback,
            channel_niche=channel_niche,
            language=language
        )

        try:
            best_eval = critic.evaluate_script(
                script_data=best_script.model_dump(),
                topic=best_script.title,
                channel_style=channel_niche,
                primary_entity=getattr(best_script, "primary_entity", None)
            )
        except Exception as e:
            logger.warning(f"Lỗi chấm điểm sau khi nhận feedback: {e}")
            return best_script, None

        if best_eval.overall_score >= min_acceptable_score and best_eval.passed:
            logger.info(f"✅ [ContentAgent] Kịch bản sau góp ý đạt chuẩn ngay lần 1: {best_eval.overall_score:.1f}/10")
            return best_script, best_eval

        # 2. Vòng lặp cải thiện: Vừa giữ trọn vẹn yêu cầu của người dùng, vừa khắc phục điểm yếu của Critic
        logger.info(
            f"⚠️ [ContentAgent] Kịch bản sau góp ý đạt {best_eval.overall_score:.1f}/10 (< {min_acceptable_score}). "
            f"Tiến hành tối ưu tự động để vừa thỏa mãn góp ý, vừa đạt điểm chất lượng cao..."
        )

        lang_low = str(language or "en").lower()
        if lang_low.startswith("vi"):
            target_lang_note = "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
        elif lang_low.startswith("pt"):
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% BRAZILIAN PORTUGUESE (PORTUGUÊS DO BRASIL). All rewritten narration, hook, and title MUST be in natural Brazilian Portuguese."
        else:
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."

        for attempt in range(1, max_attempts + 1):
            critiques_list = list(best_eval.actionable_critiques) if best_eval.actionable_critiques else []
            combined_directive = (
                f"YÊU CẦU CỐT LÕI CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ 100%): \"{feedback}\"\n"
                f"{target_lang_note}\n"
                f"ĐỒNG THỜI KHẮC PHỤC CÁC LỖI CRITIC SAU ĐỂ ĐẠT CHUẨN VIRAL: " + " | ".join(critiques_list[:3])
            )

            try:
                candidate_script = self.refine_script(
                    current_script=best_script,
                    feedback=combined_directive,
                    channel_niche=channel_niche,
                    language=language
                )

                candidate_eval = critic.evaluate_script(
                    script_data=candidate_script.model_dump(),
                    topic=candidate_script.title,
                    channel_style=channel_niche,
                    primary_entity=getattr(candidate_script, "primary_entity", None)
                )

                logger.info(
                    f"🔄 [ContentAgent Feedback-Retry {attempt}/{max_attempts}] "
                    f"Điểm mới: {candidate_eval.overall_score:.1f}/10 (Cũ: {best_eval.overall_score:.1f}/10)"
                )

                if candidate_eval.overall_score > best_eval.overall_score:
                    best_script = candidate_script
                    best_eval = candidate_eval

                if candidate_eval.overall_score >= min_acceptable_score and candidate_eval.passed:
                    logger.info(f"🎉 [ContentAgent] Kịch bản tinh chỉnh đạt điểm xuất sắc: {candidate_eval.overall_score:.1f}/10!")
                    return candidate_script, candidate_eval

            except Exception as e:
                logger.warning(f"Lỗi khi tinh chỉnh kịch bản theo góp ý lần {attempt}: {e}")
                continue

        return best_script, best_eval

    def suggest_viral_ideas(
        self,
        channel_id: str = "ch_psychology_of_the_world",
        channel_name: str = "Psychology of the world",
        channel_niche: str = "psychology",
        language: str = "en",
        recent_titles: Optional[List[str]] = None,
        count: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Sinh danh sách các chủ đề Shorts triệu view bám sát nội dung thực tế của kênh:
        - Đọc danh sách các video đã đăng/sản xuất của kênh để tránh trùng lặp.
        - Trích xuất động Top 3 Winners & Bottom 3 Losers từ DB thực tế (APV%, Stay%, Swipe%, Views).
        - Xây dựng cụm chủ đề liên hoàn (Topic Cluster / Binge-watching Playlist) kích thích xem tiếp.
        """
        from app.database.models import normalize_channel_id
        channel_id = normalize_channel_id(channel_id)
        is_psychology = "psychology" in channel_niche.lower() or "tâm lý" in channel_niche.lower()

        live_feedback_block = ""
        try:
            from app.services.video_library import get_weekly_winners_and_losers
            wl = get_weekly_winners_and_losers(channel_id, top_k=3)
            live_feedback_block = wl.get("prompt_block", "")
        except Exception as e:
            logger.debug(f"[ContentAgent] Could not fetch live winners/losers for {channel_id}: {e}")

        recent_context_block = ""
        clean_titles = [f"  - \"{t}\"" for t in (recent_titles or [])[:8] if t]
        recent_titles_str = chr(10).join(clean_titles) if clean_titles else "  - (See live channel database telemetry below)"
        recent_context_block = f"""
═══════════════════════════════════════════════════════════
📺 CHANNEL IDENTITY & LIVE PERFORMANCE TELEMETRY:
Channel ID: '{channel_id}' | Channel Name: '{channel_name}' | Niche: '{channel_niche}'
Recently Produced / Uploaded Videos on this Channel (DO NOT DUPLICATE):
{recent_titles_str}

{live_feedback_block}

CRITICAL CHANNEL ALIGNMENT & BINGE-WATCHING RULES:
1. DEEP CHANNEL SYNERGY: The {count} suggested concepts MUST feel like natural sister episodes in a binge-worthy playlist for '{channel_name}'.
2. NO DUPLICATES: DO NOT suggest topics or titles already covered in the recent videos list or live telemetry above!
3. DATA-DRIVEN REPLICATION: Replicate the hook architecture, pacing, and curiosity angle of the Top 3 Proven Winners above while strictly avoiding the structural patterns of the Bottom 3 Losers.
═══════════════════════════════════════════════════════════
"""

        fallback_psychology_en = [
            {
                "topic": "3 Subtle Signs Someone is Secretly JEALOUS of You",
                "title": "3 Signs Someone is Secretly JEALOUS of You 🐍 #psychology",
                "hook": "If a close friend does this ONE thing when you succeed, watch out...",
                "angle": "Micro-expressions, backhanded compliments, downplaying achievements",
                "pinned_comment": "Which sign hit closest to home? 1, 2, or 3? Be honest 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "155% - 165%",
                "why_viral": "Nối tiếp hoàn hảo video 'Using You' — vạch trần bạn bè giả tạo, kích thích comment cực mạnh"
            },
            {
                "topic": "4 Body Language Signs Someone is Lying to Your Face",
                "title": "4 Body Language Signs Someone is LYING to You 🤫 #psychology",
                "hook": "Top FBI interrogators look for this exact 2-second throat twitch...",
                "angle": "Swallowing reflex, eye direction change, frozen posture, speech delay",
                "pinned_comment": "Have you ever caught someone using sign #2? What happened? 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "150% - 160%",
                "why_viral": "Trang bị kỹ năng tự vệ xã hội, người xem bấm lưu video (Save) cực cao"
            },
            {
                "topic": "How Toxic People Test Your Boundaries Before Using You",
                "title": "How Toxic People TEST You Before Using You ♟️ #psychology",
                "hook": "Manipulators NEVER attack you on day one. They start with this tiny test...",
                "angle": "The fake vulnerability test, subtle favor test, crossing time boundaries",
                "pinned_comment": "If you recognize sign #1, walk away immediately. Have you experienced this? 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "160% - 170%",
                "why_viral": "Phần tiếp theo trực tiếp của '3 Signs They're Using You' — giải mã bước đi đầu tiên của kẻ thao túng"
            },
            {
                "topic": "3 Signs Someone Has a CRUSH on You but Tries to Hide It",
                "title": "3 Signs Someone Has a CRUSH on You (Secretly) 👀 #psychology",
                "hook": "If someone likes you secretly, their body will betray them in 3 ways...",
                "angle": "Pupil dilation, feet alignment, nervous tactile fiddling with drinks/jewelry",
                "pinned_comment": "Did you think of someone specific while watching this? Tag them or drop an emoji 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "145% - 155%",
                "why_viral": "Chủ đề tình cảm tò mò muôn thuở, người xem share cho bạn bè rất mạnh"
            },
            {
                "topic": "3 Dark Psychology Tricks to NEVER Be Ignored Again",
                "title": "3 Dark Psychology Tricks to NEVER Be Ignored 🧠 #psychology",
                "hook": "If someone is ignoring your messages, DO NOT double text. Use this...",
                "angle": "Strategic silence, status reversal, open loop curiosity trigger",
                "pinned_comment": "Double tap to claim your power, and tell me: Which trick will you use first? 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "150% - 165%",
                "why_viral": "Đánh vào cái tôi bị tổn thương, trao quyền lực tức thì cho người xem"
            }
        ]

        fallback_psychology_vi = [
            {
                "topic": "3 Dấu Hiệu Tinh Vi Cho Thấy Ai Đó Đang Ngầm GHEN TỊ Với Bạn",
                "title": "3 Dấu Hiệu Cho Thấy Ai Đó Đang Ngầm GHEN TỊ Với Bạn 🐍 #tamlyhoc",
                "hook": "Nếu một người bạn thân làm điều này khi bạn thành công, hãy cẩn thận...",
                "angle": "Vi biểu cảm, lời khen đểu, dập tắt niềm vui của người khác",
                "pinned_comment": "Bạn từng gặp dấu hiệu số mấy ngoài đời thực? Chia sẻ thật lòng bên dưới 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "155% - 165%",
                "why_viral": "Chạm đúng tâm lý bức xúc vì bị đồng nghiệp hoặc bạn thân đố kỵ"
            },
            {
                "topic": "4 Ngôn Ngữ Cơ Thể Vạch Trần Kẻ Đang NÓI DỐI Trước Mặt Bạn",
                "title": "4 Dấu Hiệu Cơ Thể Vạch Trần Kẻ NÓI DỐI Trắng Trợn 🤫 #tamlyhoc",
                "hook": "Các chuyên gia thẩm vấn tội phạm luôn nhìn vào phản xạ nuốt nước bọt này...",
                "angle": "Cổ họng co thắt, mắt chớp nhanh, tay chạm mũi hoặc gáy",
                "pinned_comment": "Bạn đã từng bắt quả tang ai nói dối bằng dấu hiệu số 2 chưa? 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "150% - 160%",
                "why_viral": "Kiến thức thực chiến, người xem bấm Tim và Lưu video cực cao"
            },
            {
                "topic": "Cách Kẻ Thao Túng Dò Xét Giới Hạn Trước Khi LỢI DỤNG Bạn",
                "title": "Cách Kẻ Thao Túng Thử Thách Bạn Trước Khi LỢI DỤNG ♟️ #tamlyhoc",
                "hook": "Kẻ thao túng không bao giờ lộ mặt ngay. Chúng bắt đầu bằng bài test nhỏ này...",
                "angle": "Nhờ vả vặt vãnh, giả vờ yếu đuối để thử mức độ dễ dãi",
                "pinned_comment": "Nếu thấy dấu hiệu số 1, hãy dứt khoát ngay. Bạn có từng bị dính bẫy này? 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "160% - 170%",
                "why_viral": "Nội dung cảnh báo tự vệ, kích thích tranh luận dữ dội"
            },
            {
                "topic": "3 Dấu Hiệu Ai Đó Thích Thầm Bạn Nhưng Cố Tình Giấu Kín",
                "title": "3 Dấu Hiệu Ai Đó Đang THÍCH THẦM Bạn Mà Cố Giấu 👀 #tamlyhoc",
                "hook": "Dù người ấy có che giấu giỏi đến đâu, cơ thể họ sẽ phản bội qua 3 điều này...",
                "angle": "Đồng tử giãn to, hướng mũi chân, hành động bối rối nghịch đồ vật",
                "pinned_comment": "Xem xong video này bạn có nhớ đến ai không? Thả nhẹ một icon nhé 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "145% - 155%",
                "why_viral": "Tò mò cảm xúc tuổi trẻ, tỷ lệ chia sẻ cho bạn bè cực lớn"
            },
            {
                "topic": "3 Mẹo Tâm Lý Tối Khiến Đối Phương KHÔNG THỂ Ngó Lơ Bạn",
                "title": "3 Đòn Tâm Lý Khiến Đối Phương Không Thể Ngó Lơ Bạn 🧠 #tamlyhoc",
                "hook": "Nếu họ đang bơ tin nhắn của bạn, ĐỪNG nhắn thêm. Hãy dùng đòn tâm lý này...",
                "angle": "Im lặng có chủ đích, đảo ngược vị thế, tạo khoảng trống tò mò",
                "pinned_comment": "Bấm thích để bảo vệ giá trị bản thân. Bạn sẽ thử chiêu số mấy đầu tiên? 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "150% - 165%",
                "why_viral": "Đánh thẳng vào nỗi đau bị phớt lờ, mang lại giải pháp hành động ngay"
            }
        ]

        prompt = f"""You are an elite YouTube Shorts Growth Hacker and Viral Strategist for the channel '{channel_name}' in niche: '{channel_niche}'.
Language: {language}
Goal: Generate {count} ULTRA-VIRAL video concepts tailored specifically to match this channel's existing identity and build upon its highest-performing video.
{recent_context_block}
CORE RETENTION PRINCIPLES:
1. High-Stakes Personal & Relational Angle: Focus on topics that make viewers fear being tricked, betrayed, or ignored (e.g. Red Flags, Covert Narcissism, Dark Psychology Laws, Body Language of Liars, Hidden Jealousy).
2. Electric Hook (First 2.5s): Pattern interrupt, urgent warning, or elite insider rule.
3. Psychological Call to Action (Double Tap & Protect, Debate in Comments, Curiosity Part 2).
4. Pinned Bait Question: A compelling question to pin at the top of comments to ignite massive debate.

Return ONLY a JSON array with exactly {count} objects:
[
  {{
    "topic": "Clean topic name",
    "title": "Viral YouTube Shorts Title with Emojis and #hashtags",
    "hook": "Opening 3-second hook line",
    "angle": "Core psychological mechanism or observable micro-behavior",
    "pinned_comment": "High-converting pinned bait question for the comment section",
    "viral_formula": "Double Tap & Protect | Debate in Comments | Curiosity Part 2",
    "predicted_retention": "150% - 165%",
    "why_viral": "Brief 1-sentence reason why this will blow up and fits the channel"
  }}
]
"""
        try:
            raw_text = self._call_api(prompt, json_mode=False, timeout=20)
            if raw_text:
                import re
                text = raw_text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()
                start = text.find("[")
                end = text.rfind("]")
                if start != -1 and end != -1 and end > start:
                    text = text[start:end+1]
                ideas = json.loads(text)
                if isinstance(ideas, list) and len(ideas) > 0:
                    return ideas[:count]
        except Exception as e:
            logger.warning(f"[ContentAgent] Lỗi khi sinh ý tưởng viral từ Gemini: {e}")

        # Fallback preset chuẩn chất lượng cao
        if is_psychology:
            return fallback_psychology_vi if language == "vi" else fallback_psychology_en
        return fallback_psychology_en[:count]

