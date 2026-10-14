import json
import logging
import requests
import time
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",      # Bản Lite siêu tốc, ổn định, thông lượng cao nhất
    "gemini-3.5-flash-lite",         # Bản 3.5 Lite mới nhất
    "gemini-3.1-flash-lite",         # Bản 3.1 Lite
    "gemini-3-flash-preview",        # Bản 3.0 Preview
    "gemini-flash-latest",           # Bản Flash tổng hợp
    "gemini-3.5-flash",              # Model 3.5 Flash
    "gemini-pro-latest",             # Pro (thử cuối để tránh kẹt 429)
]


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
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, dramatic, colloquial BRAZILIAN PORTUGUESE (português do Brasil).
  * Even if the topic, director notes, or niche instructions are in English, you MUST TRANSLATE and write ALL narration in BRAZILIAN PORTUGUESE.
  * Use vocabulary and expressions natural to Brazil: 'incrível', 'absurdo', 'impressionante', 'brutalmente', NOT European Portuguese.
  * STRICTLY FORBIDDEN: Do NOT output English or European Portuguese narration.
- TITLE REQUIREMENT:
  * 'title' MUST be 100% in BRAZILIAN PORTUGUESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- PT', '- BR', '(Brasil)'.
  * Titles must be punchy, dramatic and appeal to Brazilian curiosity (use 'isso' triggers, 'quem diria', 'o que ninguem te contou').
- thumbnail_text MUST also be in PORTUGUESE BRASILEIRO (3-5 words ALL CAPS + 1 emoji).
- NEVER mix languages (e.g. Portuguese narration with English title or vice-versa).
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
        Gọi trực tiếp Gemini REST API với timeout nghiêm ngặt và xoay key thông minh.
        Khi một API Key bị 429 (hết quota) hoặc 403 (cấm), hệ thống xoay ngay sang key tiếp theo
        thay vì lãng phí thời gian thử các model khác với cùng key đã hết quota.
        """
        import time
        start_time = time.monotonic()
        total_keys = max(len(self.rotator.keys), 1)
        attempts = 0
        max_attempts = max(total_keys * 2, 4)

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        for attempt in range(max_attempts):
            elapsed = time.monotonic() - start_time
            if elapsed >= max_total_seconds:
                raise RuntimeError(
                    f"Gemini API: Vượt tổng timeout {max_total_seconds}s "
                    f"(đã thử {attempts} requests trong {elapsed:.1f}s)"
                )

            key = self.rotator.get_key()
            if not key:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            key_failed_quota = False
            for model in AVAILABLE_MODELS:
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
                    elif r.status_code in (429, 403):
                        logger.warning(f"⚠️ Key {key[:8]}... gặp mã {r.status_code} ({model}). Xoay ngay sang key tiếp theo...")
                        self.rotator.rotate_key(cooldown_sec=45.0 if r.status_code == 429 else 180.0)
                        key_failed_quota = True
                        break  # Bỏ qua các model còn lại của key đã hết quota, chuyển sang key mới ngay
                    elif r.status_code == 503 and json_mode:
                        # Fallback: Thử lại không dùng responseMimeType (tránh 503 trên engine structured JSON)
                        logger.info(f"Gemini API ({model}) gặp 503 với structured output, thử lại chế độ raw JSON...")
                        time.sleep(0.5)
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
                        continue
                    elif r.status_code == 503:
                        logger.warning(f"⚠️ Model {model} đang quá tải tạm thời (503). Chuyển sang model tiếp theo...")
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
                    time.sleep(0.5)
                    continue

            # Nếu key chưa bị rotate do 429, xoay sang key tiếp theo sau khi đã thử các model
            if not key_failed_quota:
                self.rotator.rotate_key()

        raise RuntimeError(
            f"Đã thử tất cả {attempts} requests qua {total_keys} keys và "
            f"{len(AVAILABLE_MODELS)} models trong {time.monotonic() - start_time:.1f}s — không nhận được phản hồi."
        )

    def _parse_json_script(self, raw_text: str) -> ScriptContract:
        """Helper làm sạch markdown và parse JSON thành ScriptContract, tự động khử rò rỉ ngôn ngữ ở tiêu đề."""
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

        for sc in scenes:
            if not isinstance(sc, dict):
                continue

            # Đảm bảo visual_keyword không bị rỗng
            vk = str(sc.get("visual_keyword") or "").strip()
            if not vk:
                sq = str(sc.get("stock_query") or "").strip()
                if sq:
                    vk = sq
                else:
                    vp_words = str(sc.get("visual_prompt") or "").strip().split()
                    vk = " ".join(vp_words[:3]) if vp_words else "cinematic visual"
                sc["visual_keyword"] = vk

            # Đảm bảo visual_prompt không bị rỗng
            vp = str(sc.get("visual_prompt") or "").strip()
            if not vp:
                sc["visual_prompt"] = f"Cinematic 8k photorealistic vertical shot of {sc['visual_keyword']}"

            # Đảm bảo stock_query không bị rỗng và lọc nhẹ các từ trừu tượng cấm kỵ của Pexels
            sq = str(sc.get("stock_query") or "").strip()
            if not sq:
                sq = sc["visual_keyword"]
            else:
                for b in banned_abstract:
                    sq = re.sub(rf'\b{b}\b', '', sq, flags=re.IGNORECASE).strip()
                sq = re.sub(r'\s+', ' ', sq).strip()
                if not sq:
                    sq = sc["visual_keyword"]
            sc["stock_query"] = sq

            # Đảm bảo media_type có giá trị mặc định nếu LLM chưa chỉ định
            if not sc.get("media_type"):
                sc["media_type"] = "auto"

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
                        is_vn_script = any("\u0100" <= c <= "\u1EF9" for c in comb_text)
                        is_pt_script = any(w in comb_text for w in ("você", "não", "então", "coração", "selva", "olhos", "jamais"))
                        if is_vn_script:
                            last_sc["narration"] = "Và khoảnh khắc đó mở ra sự thật từ đầu."
                        elif is_pt_script:
                            last_sc["narration"] = "E nesse instante o ciclo recomeça diante de você."
                        elif any(k in comb_text for k in ("puppy", "kitten", "rescue", "stray", "shelter", "abandoned", "dog", "cat")):
                            last_sc["narration"] = "And that moment of trust starts his new life."
                        else:
                            last_sc["narration"] = "And that hidden mechanism starts the cycle all over."

        # ── 2.5. TỰ ĐỘNG CHIA NHỊP CẮT CẢNH DỒN DẬP (AUTO-SCENE PACING SPLITTER: 3.0s - 3.8s/CẢNH) ──
        # Đảm bảo Cảnh 1 Hook <= 10 từ (~2.2s - 2.8s) và các cảnh thân <= 13 từ (~3.2s - 3.8s)
        def _split_long_narration(text: str, prefer_word_idx: int) -> Optional[tuple]:
            text_clean = text.strip()
            words = text_clean.split()
            if len(words) < 10:
                return None

            # Ưu tiên 1: Tách theo ranh giới câu (. ! ?) nếu có >= 2 câu và mỗi vế >= 4 từ
            sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text_clean) if s.strip()]
            if len(sents) >= 2:
                acc = []
                for i_s, s_part in enumerate(sents[:-1]):
                    acc.append(s_part)
                    left_str = " ".join(acc).strip()
                    right_str = " ".join(sents[i_s + 1:]).strip()
                    if len(left_str.split()) >= 4 and len(right_str.split()) >= 4:
                        return left_str, right_str

            # Ưu tiên 2: Tách tại dấu phẩy, chấm phẩy, gạch ngang hoặc liên từ ở giữa câu (từ vị trí 4 đến len-4)
            _split_conj = {
                "until", "when", "while", "because", "where", "which", "before", "after", "but", "and", "yet",
                "mas", "enquanto", "quando", "até", "porque", "onde", "como",
                "nhưng", "khi", "bởi", "trong", "mà", "nơi"
            }
            _dangling_end = {
                "the", "a", "an", "to", "of", "in", "on", "at", "for", "with", "by", "from", "into", "that",
                "which", "who", "and", "or", "but", "because", "if", "when", "while", "as", "until",
                "o", "a", "os", "as", "de", "do", "da", "em", "no", "na", "para", "com", "por", "que", "se",
                "của", "trong", "với", "để", "tại", "vì", "do", "bởi", "nhưng", "và"
            }
            best_idx = None
            best_score = 999.0
            for idx_w in range(4, len(words) - 3):
                prev_w = words[idx_w - 1]
                curr_w_clean = re.sub(r'[^\w]', '', words[idx_w]).lower()
                prev_w_clean = re.sub(r'[^\w]', '', prev_w).lower()
                if prev_w_clean in _dangling_end:
                    continue
                has_punct = prev_w.endswith((",", ";", ":", "—", "–", "-"))
                is_conj = curr_w_clean in _split_conj
                if has_punct or is_conj:
                    dist = abs(idx_w - prefer_word_idx) - (2.5 if has_punct else 1.2)
                    if dist < best_score:
                        best_score = dist
                        best_idx = idx_w

            if best_idx is None:
                # Fallback tách ở điểm giữa nếu không dính từ treo
                mid = max(5, min(len(words) - 4, prefer_word_idx))
                for offset in (0, 1, -1, 2, -2):
                    cand = mid + offset
                    if 4 <= cand <= len(words) - 4:
                        pw = re.sub(r'[^\w]', '', words[cand - 1]).lower()
                        if pw not in _dangling_end:
                            best_idx = cand
                            break

            if best_idx is None:
                return None

            left_part = " ".join(words[:best_idx]).strip().rstrip(",;:-—– ")
            right_part = " ".join(words[best_idx:]).strip()
            if not left_part or not right_part:
                return None
            if not left_part.endswith((".", "!", "?")):
                left_part += "."
            if right_part and right_part[0].islower():
                right_part = right_part[0].upper() + right_part[1:]
            if not right_part.endswith((".", "!", "?")):
                right_part += "."
            return left_part, right_part

        if scenes and isinstance(scenes, list):
            camera_modifiers = [
                ("extreme close up macro", "Extreme macro close-up shot, razor-sharp focus, "),
                ("low angle dynamic", "Dramatic low-angle tracking shot, intense depth, "),
                ("cinematic tracking motion", "Dynamic over-the-shoulder tracking perspective, "),
                ("wide atmospheric reveal", "Sweeping wide-angle cinematic reveal, volumetric light, "),
            ]
            paced_scenes: List[Dict[str, Any]] = []
            for idx_sc, sc in enumerate(scenes):
                if not isinstance(sc, dict):
                    continue
                narr_str = str(sc.get("narration") or "").strip()
                w_list = narr_str.split()
                w_count = len(w_list)

                # Cảnh 1 (Hook): Nếu > 10 từ (~3.8s+) -> Tách ngay thành 2 cảnh nhanh (~2.4s/cảnh)
                # Cảnh thân: Nếu >= 14 từ (hoặc >= 12 từ khi tổng cảnh hiện tại < 9) và tổng cảnh chưa vượt 13 -> Tách đôi!
                should_split = (
                    (idx_sc == 0 and w_count >= 11)
                    or (idx_sc > 0 and w_count >= 14 and len(paced_scenes) + (len(scenes) - idx_sc) < 14)
                    or (idx_sc > 0 and w_count >= 12 and len(paced_scenes) + (len(scenes) - idx_sc) < 9)
                )
                target_pref = 6 if idx_sc == 0 else max(6, w_count // 2)
                split_res = _split_long_narration(narr_str, target_pref) if should_split else None

                if split_res:
                    left_narr, right_narr = split_res
                    mod_kw, mod_vp = camera_modifiers[len(paced_scenes) % len(camera_modifiers)]
                    sc_first = dict(sc)
                    sc_first["narration"] = left_narr
                    sc_first["estimated_seconds"] = round(max(2.2, len(left_narr.split()) / 2.65), 1)

                    sc_second = dict(sc)
                    sc_second["narration"] = right_narr
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
                    extra_focus = " ".join(right_nouns[:2]) if right_nouns else mod_kw.split()[0]
                    sc_second["visual_keyword"] = f"{base_vk} {extra_focus}".strip()[:65]
                    sc_second["stock_query"] = f"{base_sq.split()[0]} {extra_focus}".strip()[:40] if base_sq else extra_focus
                    sc_second["visual_prompt"] = f"{mod_vp}{base_vp}"
                    sc_second["estimated_seconds"] = round(max(2.2, len(right_narr.split()) / 2.65), 1)

                    paced_scenes.append(sc_first)
                    paced_scenes.append(sc_second)
                else:
                    sc["estimated_seconds"] = round(max(2.2, min(4.5, w_count / 2.65)), 1)
                    paced_scenes.append(sc)

            # Đánh lại số thứ tự scene_id chuẩn xác 1..N
            for new_id, p_sc in enumerate(paced_scenes, start=1):
                p_sc["scene_id"] = new_id
            scenes = paced_scenes
            raw_data["scenes"] = scenes

        # ── 2.8. ĐẢM BẢO PHÂN CẢNH CUỐI KHÉP LẠI GỌN GÀNG, KHÔNG DÍNH TỪ LỬNG ──
        if scenes and isinstance(scenes, list):
            last_scene = scenes[-1]
            if isinstance(last_scene, dict):
                last_narr = last_scene.get("narration", "").strip()
                # Loại bỏ các đuôi nối lửng lơ như "because...", "which is why..."
                clean_narr = re.sub(r'[\s\.\,\-\—\…]+(?:because|which|and|so|that|but|with|or|while|as|if|bởi vì|cho nên)[\s\.\,\-\—\…]*$', '', last_narr, flags=re.IGNORECASE)
                clean_narr = clean_narr.strip().rstrip(".,-—…")
                if clean_narr and not clean_narr.endswith((".", "!", "?")):
                    clean_narr += "."
                last_scene["narration"] = clean_narr

        # Cập nhật lại full_narration để đồng bộ 100% với các scene
        raw_data["full_narration"] = " ".join(s.get("narration", "") for s in scenes if isinstance(s, dict))
        if "hook" not in raw_data or not raw_data["hook"]:
            raw_data["hook"] = scenes[0].get("narration", "") if scenes and isinstance(scenes[0], dict) else raw_data.get("title", "")
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
        if "pinned_comment" not in raw_data or not str(raw_data.get("pinned_comment", "")).strip():
            comb = f"{str(raw_data.get('topic', ''))} {str(raw_data.get('title', ''))}".lower()
            is_vn = any("\u0100" <= c <= "\u1EF9" for c in comb)
            if any(k in comb for k in ("rescue", "chó", "mèo", "pet", "cứu hộ", "puppy", "kitten", "animal")):
                raw_data["pinned_comment"] = (
                    "Nếu là bạn, bạn có dừng lại giúp chú cún này không? 🥺 Link ủng hộ trạm cứu hộ mình đã ghim ở đầu kênh nhé! ❤️"
                    if is_vn else
                    "If you were there, would you have stopped to help this poor baby? 🥺 Our rescue support link is pinned at the top of our channel bio! ❤️"
                )
            elif any(k in comb for k in ("tâm lý", "psychology", "manipulat", "thao túng", "mindset", "gaslight", "liar", "dark psychology", "tội phạm", "killer")):
                raw_data["pinned_comment"] = (
                    "Bạn đã từng gặp ai có dấu hiệu số 2 trong đời thực chưa? Chia sẻ thật lòng bên dưới nhé 👇"
                    if is_vn else
                    "Have you ever encountered someone who showed sign #2 in real life? Tell me your story below 👇"
                )
            elif any(k in comb for k in ("lịch sử", "history", "what if", "thế chiến", "war", "rockefeller", "conqueror")):
                raw_data["pinned_comment"] = (
                    "Nếu đứng ở vị trí của họ, bạn sẽ đưa ra quyết định gì? Liệu lịch sử có rẽ sang hướng khác không? Tranh luận ngay bên dưới 👇"
                    if is_vn else
                    "What would you have done if you stood in their shoes? Would history be completely different? Debate below 👇"
                )
            elif any(k in comb for k in ("deep sea", "biển sâu", "ocean", "đại dương", "mystery", "bí ẩn", "abyss", "blind", "silence")):
                raw_data["pinned_comment"] = (
                    "Bạn đã bao giờ trải qua hiện tượng kỳ lạ này ngoài đời thực chưa? Để lại câu chuyện của bạn bên dưới 👇"
                    if is_vn else
                    "Have you ever experienced this eerie phenomenon in real life? Drop your thoughts below 👇"
                )
            elif any(k in comb for k in ("săn mồi", "predator", "wildlife", "dã thú", "báo", "sư tử", "hổ", "cheetah", "leopard", "eagle", "onça", "sucuri")):
                raw_data["pinned_comment"] = (
                    "Theo bạn kẻ săn mồi nào sẽ chiến thắng trong cuộc đối đầu sinh tử này? Để lại bình chọn bên dưới 👇"
                    if is_vn else
                    "Who wins this ultimate apex duel in the wild? Drop your vote below 👇"
                )
            else:
                raw_data["pinned_comment"] = (
                    "Chi tiết nào khiến bạn bất ngờ nhất? Hãy để lại bình luận của bạn bên dưới nhé 👇"
                    if is_vn else
                    "Which moment shocked you the most? Drop your thoughts in the comments below 👇"
                )

        return ScriptContract(**raw_data)


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
        lang_clean = (language or "en").strip().lower()
        if lang_clean.startswith("vi"):
            target_lang_display = "TIẾNG VIỆT"
        elif lang_clean.startswith("pt"):
            target_lang_display = "PORTUGUÊS BRASILEIRO"
        else:
            target_lang_display = "ENGLISH"

        is_rescue = "rescue" in (channel_niche or "").lower() or "animal" in (channel_niche or "").lower()
        duration_spec = "42 ĐẾN 52 GIÂY (9 ĐẾN 12 PHÂN CẢNH NHANH 3.2-3.8s/CẢNH, STRICTLY 95 ĐẾN 130 TỪ)" if is_rescue else "35 ĐẾN 46 GIÂY (9 ĐẾN 12 PHÂN CẢNH NHANH 2.8-3.8s/CẢNH, STRICTLY 85 ĐẾN 120 TỪ)"
        word_range_en = "95 to 130 words (42-52s 4-Stage Transformation Arc, 9-12 rapid scenes of 7-11 words each)" if is_rescue else "85 to 120 words (35-46s 4-Beat Payoff Arc, 9-12 rapid scenes of 7-11 words each)"

        prompt = f"""You are an elite YouTube Shorts director and viral retention engineer for the channel '{niche_profile.get('name')}' (niche: '{channel_niche}').
Topic: '{topic}'
Target Language: {language} ({target_lang_display})
{lang_instruction}
{director_block}
{niche_instructions}
═══════════════════════════════════════════════════════════
YOUTUBE SHORTS TEST FUNNEL & VIRAL RETENTION ENGINE (VƯỢT NGƯỠNG 10,000 – 50,000+ VIEWS)
YouTube đánh giá video Shorts qua 2 vòng thử thách:
- Vòng 1 (100 - 300 test pool): Cần Viewed > 75% (Swipe-Away < 25%) nhờ Hook 2.5s đầu (<= 9 từ) + Tiêu đề kích thích tò mò.
- Vòng 2 (Vượt 10,000 - 50,000 view để lên 100k - 1M view): Thuật toán đòi hỏi APV > 95% - 115% (Average Percentage Viewed) + Nhịp cắt cảnh dồn dập (9-12 cảnh, 2.8s - 3.8s/cảnh) + Kết thúc Vòng lặp Vô tận (Zero-Outro Infinite Loop).
  * CẢNH BÁO TỬ HUYỆT APV: Tuyệt đối KHÔNG đọc câu kêu gọi "Subscribe right now...", "Leave a comment...", "Inscreva-se...", "Đăng ký kênh..." ở cảnh cuối! Khi người xem nghe thấy câu kêu gọi đăng ký/bình luận ở cuối video, não bộ họ lập tức biết câu chuyện đã hết và vuốt đi sớm 5-7 giây (làm tụt 15% APV)!
═══════════════════════════════════════════════════════════
1. THỜI LƯỢNG CHUẨN VÀNG, NHỊP CẮT 3.2 GIÂY & CẤU TRÚC 4 NHỊP GIỮ CHÂN (target: {word_range_en}):
   - THỜI LƯỢNG & SỐ CẢNH BẮT BUỘC: {duration_spec}. Mỗi cảnh chỉ từ 7 đến 11 từ (tương ứng 2.5 - 3.8 giây) để đổi góc máy liên tục!
   - CẤU TRÚC 4 NHỊP BẮT BUỘC ĐỂ ĐẠT APV > 100% & BỨT PHÁ VIEW:
     * NHỊP 1: 2.5-SECOND THUMB-STOPPER HOOK (0 - 2.8s — Cảnh 1):
       Cảnh 1 BẮT BUỘC cực ngắn và sắc: 5 đến 9 từ (~2.5 giây)! Nêu bật nghịch lý nhức nhối, đối đầu sinh tử cụ thể (A vs. B), lời cảnh báo trực diện ("You", "Your brain", "They") hoặc khoảnh khắc nghịch lý gây tò mò ("He refused to let go until...").
     * NHỊP 2: RAPID ESCALATION & 12-SECOND MID-VIDEO RE-HOOK (2.8 - 18s — Cảnh 2 đến Cảnh 5):
       [CHỐT CHẶN CHỐNG RƠI RỤNG Ở GIÂY 12-15]:
       - Cảnh 2, 3, 4 đưa ngay dữ kiện/hành động vật lý cụ thể với góc máy thay đổi dồn dập mỗi 3.2 giây.
       - Đặc biệt tại CẢNH 4 hoặc CẢNH 5 (~giây 12-15): BẮT BUỘC tung một "Mid-Video Re-Hook / Pattern Interrupt Twist" ("Nhưng đó chưa phải điều đáng sợ nhất...", "Until a hidden camera revealed the real reason...", "But in one single second, the hunter became the prey...") để khóa chặt người xem đi tiếp vào nửa sau của video!
     * NHỊP 3: THE HIGH-VALUE PAYOFF — CÚ GIẢI THOÁT & GIÁ TRỊ CỐT LÕI (18 - 38s — Cảnh 6 đến Cảnh 9/11):
       [QUAN TRỌNG NHẤT ĐỂ VƯỢT NGƯỠNG 10K - 50K VIEW & CHUYỂN ĐỔI SUBSCRIBER TỰ NHIÊN]
       - ĐÂY LÀ PHẦN THƯỞNG CHO NGƯỜI XEM (THE PAYOFF): Tuyệt đối không để video kết thúc lửng lơ hoặc cắt cụt trước khi giải quyết vấn đề!
       - BẮT BUỘC cung cấp:
         1. Với Psychology / Mysteries / Alternate History: Lật tẩy bản chất ngầm (The Revelation) + Đòn phản công thực chiến (Actionable Counter-Move) hoặc toàn cảnh thế giới/giác quan bị đảo lộn.
         2. Với Animal Rescue: Phải thể hiện trọn vẹn màn lột xác cảm xúc (Healing & Joyful Transformation) — từ sợ hãi đến khoảnh khắc dám chạm vào tay người cứu hộ và chạy nhảy hạnh phúc dưới ánh nắng.
         3. Với Wildlife Predators: Phân định rõ đòn kết liễu cơ sinh học (PSI, tốc độ, chiến thuật) và kẻ chiến thắng cuối cùng trong cuộc đối đầu.
     * NHỊP 4: ZERO-OUTRO CLIMAX & SEAMLESS INFINITE LOOP BRIDGE (Cảnh cuối — 2.5 - 3.5s):
       - CẤM TUYỆT ĐỐI đọc lời kêu gọi "Subscribe", "Follow", "Leave a comment", "Inscreva-se", "Đăng ký kênh" trong lời thoại!
       - Cảnh cuối phải là ĐỈNH ĐIỂM CỦA SỰ THẬT (Final Twist / Punchline) và kết thúc bằng một câu cầu nối khép kín ngữ pháp nhưng mở ra vòng lặp ý niệm nối thẳng về câu Hook ở Cảnh 1 (tạo APV 105% - 125%).

2. 2.5-SECOND SHOCK ANGLE HOOK (SCENE 1) & HIGH-CTR TITLE RULES:
   - The first 1.5 - 2.5 seconds determines 100% of Shorts performance (Viewed vs Swiped away).
   - HIGH-CTR TITLE MANDATE (LEARNED FROM CHANNEL ANALYTICS):
     * NEVER write bleak/depressing-only titles for Animal Rescue (FORBIDDEN: "Frozen Puppy Found in Rain", "Shivering Puppy Tied In Rain"). ALWAYS build a curiosity/transformation gap (WINNING FORMULA: "He Refused to Let Go Until This Happened 🥺❤️", "The Exact Moment He Realized He Was Finally Safe 🐾").
     * NEVER write vague generic titles for Wildlife (FORBIDDEN: "Invisible Monsters Waiting To Strike") or mix sports/off-niche words ("Bayern"). ALWAYS name the specific apex predators or duel ("Green Anaconda vs. Black Caiman: Brutal River Duel! 🐍🐊", "Why Lions REFUSE to Fight The Honey Badger 🦡🦁").
     * NEVER repeat saturated numbers like "11,000m" on Mysteries; use visceral second-person sensory/fear hooks ("Your Eyes Are Biologically Blind! 👁️", "Why Your Mind Snaps In Absolute Silence 🧠").
   - SCENE 1 HOOK FORMULAS (MUST CHOOSE 1 OF 4 CORE FORMULAS):
     * Formula 1 (Personal Pain Point & Hidden Vulnerability - HIGHEST RETENTION):
       Target personal insecurity, hidden exploitation, quiet anxiety, or counter-intuitive psychology using personal triggers ("You", "Your brain", "They"):
       - "3 subtle signs they are JUST using your kindness..."
       - "Stop trying to be confident. Do this instead..."
       - "If you feel drained around them, watch out..."
       - "Your brain starts self-destructing the second you do this..."
       - "He refused to let go until this happened..."
     * Formula 2 (Apex Duel / Urgent Danger): "When a 20-foot Anaconda ambushes a Caiman, only one survives..." / "If you notice this symbol, leave immediately..."
     * Formula 3 (Pattern Interrupt / Elite Authority Secret): "The FBI trick that exposes any liar in 2 seconds..." / "The forbidden rule 99% of people discover too late..."
     * Formula 4 (Dark Paradox / Counterfactual Twist): "What if the Roman Empire never fell, and ruled today?"
   - NEVER start with textbook definitions, greetings, or passive rhetorical questions ("Did you know...", "Welcome to...").
   - Scene 1 spoken text MUST be strictly 5 to 9 words (2.0s - 2.8s), delivered with high stakes, sharp rhythm, and immediate urgency.
   - Scene 1 visual_keyword & visual_prompt MUST be visually arresting, shocking, and intense.
   - thumbnail_text MUST be strictly 3 to 5 words in ALL CAPS with 1 high-contrast emoji (e.g. "THEY'RE USING YOU 🚩", "ANACONDA VS CAIMAN 🐍", "HE REFUSED TO LET GO 🥺", "ROME NEVER FELL? 🏛️"). This text is rendered directly on Frame 0.0s as the massive Hook Headline Banner to stop the viewer's thumb from swiping away!

3. SEAMLESS INFINITE LOOP ENDING (FINAL SCENE 2.5-3.5s — NO SPOKEN OUTRO CTA):
   - ZERO SPOKEN OUTRO CTA RULE (CRITICAL FOR >100% APV):
     * NEVER include "Subscribe", "Like", "Follow", "Leave a comment", "Leave your theory in the comments", "Inscreva-se", "Deixe nos comentários", or "Đăng ký kênh" in ANY scene's spoken narration!
     * Engagement bait belongs ONLY in the `pinned_comment` JSON field, NEVER in the voiceover narration.
   - SEAMLESS CONCEPTUAL CONTINUITY (THE INFINITE LOOP BRIDGE):
     * The final scene (7-10 words) MUST deliver the final high-impact payoff/revelation and bridge seamlessly back into Scene 1's opening hook so viewers rewatch before realizing the video looped!
     * Examples of high-APV final scene loop bridges:
       - Psychology: "And once you spot that micro-expression, their entire mask shatters." (loops straight back to Scene 1)
       - Animal Rescue: "Today he runs into warm arms, proving love heals every scar." (loops back to Scene 1's contrast)
       - Mysteries / Deep Sea: "And that terrifying biological blind spot is happening inside you right now."
       - Wildlife: "In the dark Amazon waters, even the apex king becomes prey."
   - HARD GRAMMAR & COMPLETENESS RULE:
     * Spoken narration MUST be 100% grammatically complete with proper closing punctuation (period, exclamation, or question mark).
     * NEVER end the script with dangling prepositions or incomplete conjunctions like 'because', 'which', 'and', 'so', 'that', 'but', 'with', 'bởi vì', etc.

4. CONCRETE PHYSICAL STOCK QUERIES (NO ABSTRACT WORDS & NO BORING DARK CLICHES):
   - stock_query is 2-3 English search words strictly for Pexels stock video/photo search.
   - HARD RULE: stock_query MUST be a CONCRETE, VISIBLE, PHYSICAL OBJECT, PERSON, OR ACTION.
   - STRICTLY FORBIDDEN abstract words: "psychology", "minimalism", "authority", "power", "wealth", "mindset", "discipline", "history", "secret", "truth".
   - STRICTLY FORBIDDEN boring monotonous dark clichés: "man in shadows", "walking dark street", "sad person darkness", "dark moody rain", "pitch black".
   - INSTEAD, USE VIVID, CREATIVE, REAL-WORLD METAPHORS & ACTIONS:
     * Confidence / Power -> "confident businessman walking", "woman speaking stage", "eagle soaring mountain"
     * Strategy / Manipulation -> "chess game hand move", "dominoes falling row", "hand signing contract"
     * Time / Anxiety -> "hourglass sand falling", "golden pocket watch ticking", "coffee cup steam morning"
     * Clarity / Truth -> "magnifying glass text", "light bulb illuminating", "sun rising over city"
     * Human interaction -> "two people talking cafe", "handshake business deal", "crowd walking sunny street"
     * Science / Discovery -> "scientist looking microscope", "dna glowing animation", "laboratory glassware test"
     * Nature / Deep Sea -> "bioluminescent jellyfish glowing", "majestic whale swimming", "sunbeams through ocean surface"

5. MASTER VISUAL DIRECTOR & CINEMATIC PROMPT (FOR AI IMAGERY & ARTWORK):
   - As the Master Director of Photography, NEVER create flat, monotonous, or muddy dark images!
   - Paint with light, rich color palettes, and cinematic depth appropriate to the topic's spirit:
     * Golden Hour & Warm Sunlight: For breakthroughs, hope, ancient history, majestic landscapes, victory, morning routines.
     * Crisp High-Key & Editorial Architectural Light: For modern psychology, business strategy, high intellect, clean minimalist spaces, boardroom interactions.
     * Rich Cyberpunk / Vivid Neon: For cutting-edge AI, future tech, quantum computing, digital mindscapes.
     * Cinematic Twilight / Deep Sapphire: For profound contemplation, cosmic wonders, serene ocean vistas.
     * Bioluminescent Glow & Prismatic Caustics: For deep sea exploration, neuro-synapses firing, alien biology.
     * Dramatic High-Contrast Spotlight: For tense psychological interrogations, unmasking deception, pivotal turning points.
   - STRICT RULE: BANNED MONOTONOUS TROPES!
     * NEVER default to "pitch-black alley", "man in shadows", "dark gloomy room with smoke", or "muddy chiaroscuro" unless the topic is explicitly about a midnight horror anomaly.
     * Each scene MUST feel distinct in composition, color temperature, and perspective!
   - Format: Put the MAIN SUBJECT & ACTION FIRST:
     "[Focal Subject + Specific Micro-Action], [Environment & Setting], [Camera Angle: macro close-up / low-angle dolly / wide establishing shot], [Lighting & Color Grading: warm golden sunbeams / crisp studio editorial / vivid neon / crystalline water caustics]"
   - No literal metaphors: always illustrate the actual physical scene or observable behavior.
   - Every scene must be visually unique (no repeating subjects or camera angles).

6. TITLE & LANGUAGE INTEGRITY (100% STRICT ENFORCEMENT):
   - The entire video script (spoken narration in all scenes, hook, full_narration, title) MUST be 100% in {target_lang_display}.
   - Title MUST be in {target_lang_display} and strictly focused on click-through rate (CTR > 10%).
   - Clean, punchy title under 60 characters with emojis.
   - DO NOT include channel tags, country names, or suffixes like "- VN", "- VI", "(Vietnam)", "- EN".

7. DYNAMIC PRIMAL VISUAL HOOK ENGINE (SCENE 1 ONLY — HIGHEST CTR IMPACT):
   Scene 1 visual is the SINGLE MOST IMPORTANT factor preventing swipe-away (>75% viewed).
   DO NOT depict the topic literally or passively (FORBIDDEN: person sitting at desk, generic office, landscape without focal point, talking head, casual pose).
   Instead, apply the universal laws of VISUAL PSYCHOLOGY to deduce the most potent biological curiosity trigger for '{topic}':
   
   A. THE 5 UNIVERSAL PRIMAL TRIGGERS:
      1. Scale Shock & Unseen Terror (Thalassophobia, Megalodon, colossal towering titan, cosmic abyss, gargantuan shadows dwarfing a human).
      2. Magnetic Attraction & Mystery (Captivating charismatic presence, alluring glance over shoulder, high-fashion editorial styling, magnetic confidence).
      3. Psychological Conflict & Visceral Tension (Deduce the invisible friction between facade and reality; strictly ban lazy dark faces, isolated eye close-ups, and repetitive canned tropes).
      4. Imminent Catastrophe & Collapse (Burning empire ruins, shattered glass mid-explosion, venomous fangs dripping, golden crown tumbling on sunlit marble).
      5. Forbidden Wealth & Forbidden Power (Gleaming gold vault illuminated by warm spotlights, raining cash, modern high-stakes billionaire boardroom).
   
   B. HỆ THỐNG TƯ DUY LOGIC SUY LUẬN ĐỘC LẬP (FIRST-PRINCIPLES AUTONOMOUS DEDUCTION):
      Đạo diễn AI PHẢI tư duy logic tự thân từ kịch bản, TUYỆT ĐỐI KHÔNG dùng từ khóa đóng đinh hay sao chép mẫu cố định:
      1. Bước 1 - Phân tích Mâu thuẫn Nhân quả (Causal Conflict Analysis):
         Đọc kỹ kịch bản của '{topic}'. Tự đặt câu hỏi: "Mâu thuẫn ngầm lớn nhất ở đây là gì? Ai đang che giấu điều gì? Đâu là sự bất thường khiến não bộ người xem lập tức giật mình?".
      2. Bước 2 - Suy Luận Chuyển Hóa Thành Tương Tác Vật Lý Cụ Thể (Physical Embodiment Reasoning):
         Không ai có thể "chụp ảnh một khái niệm trừu tượng". Hãy tự suy luận một hành vi vi mô, một tương tác giữa 2 chủ thể, hoặc một vật chứng vật lý cụ thể trong đời thực để chứng minh mâu thuẫn đó.
         Mỗi chủ đề phải sinh ra một bối cảnh và tương tác hoàn toàn mới mẻ, độc nhất, bám sát nội dung kịch bản đó!
      3. Bước 3 - Bố Cục Quang Học Điện Ảnh (Optical Hierarchy & Cinematic Lighting):
         - Luôn thiết lập ánh sáng tương phản điện ảnh (Key Light rọi rõ nét chủ thể, Rim Light viền sáng tách chủ thể khỏi nền tối).
         - Chủ thể phải sắc nét, sống động, ánh sáng ấm hoặc tương phản rõ rệt. CẤM TUYỆT ĐỐI khuôn mặt tối màu vô hồn, cấm cận cảnh mắt trần trụi, cấm tượng đá vô tri!

8. PHYSICAL ACTION & SENSORY MAPPING (ALL SCENES MUST HAVE TANGIBLE SIGHTS):
   - CRITICAL FIDELITY FOR ANIMALS & NATURE (STRICT RULE):
     If the topic or command mentions animals, birds, wildlife, pets, or nature (e.g. chim, đại bàng, thú hoang, sư tử, hổ, chó, mèo, cá heo):
     * 100% of scenes MUST directly show the REAL LIVING ANIMAL, its chicks/cubs, hunting, feeding, nesting, and wild habitat!
     * NEVER hallucinate human equipment (cameras, film rolls, darkrooms), cameramen, or human organs (human eye, DNA helix) when the topic is about animals!
     * All 'stock_query' and 'visual_keyword' MUST be concrete animal terms (e.g. 'mother bird feeding chicks', 'baby birds in nest', 'eagle flying across storm', 'hawk diving')!
   - In EVERY scene, use autonomous logical deduction to translate abstract thoughts or emotions into OBSERVABLE PHYSICAL BEHAVIORS:
     * Dựa trên cốt truyện cụ thể, đạo diễn tự suy luận hành vi hoặc tương tác vật lý chân thực nhất (người run rẩy, cử chỉ lén lút, phản ứng né tránh, ánh nhìn bất an, hành động che đậy).
     * Tuyệt đối không lặp lại khuôn mẫu cũ hay từ khóa sáo rỗng.

9. DYNAMIC AUTONOMOUS MEDIA SELECTION (`media_type` PER SCENE):
   For EACH scene, act as the Master Director of Photography and autonomously decide what visual medium is objectively best suited for this exact moment:
   - 'ai_image': MANDATORY FOR PSYCHOLOGY, PHILOSOPHY, STOICISM, MINDSET, DARK PSYCHOLOGY, DEEP MYSTERIES, AND METAPHORS!
     Stock video is repetitive and generic for deep psychological nuance. You MUST choose 'ai_image' for at least 50% - 75% of scenes (expressive psychological micro-expressions, glowing neural networks, majestic statues under golden sunrise, mind palace architectural metaphors). These are rendered with 8K Flux.1 / Imagen and dynamic 3D camera parallax motion!
   - 'video': Real-world physical action, urban life, footsteps, hands, clock ticking, nature, or wildlife (searches real Pexels 9:16 Video B-Roll).
   - 'photo': Authentic high-resolution real-world still photography or modern realistic architecture.
   - 'documentary': Authentic historical figures (e.g. Einstein, Napoleon), real artifacts, or historical events (searches Wikimedia / historical archive).
   DO NOT default all scenes to one single type! Intelligently vary and balance media types across scenes for maximum viewer engagement.

10. NỘI DUNG CÓ CHIỀU SÂU TRI THỨC & CHẠM CẢM XÚC SỐ ĐÔNG (FACT-DENSE STORYTELLING & ANTI-FLUFF):
   - TUYỆT ĐỐI CẤM CÂU ĐỆM RỖNG TUẾCH (ZERO-FLUFF POLICY): Cấm các câu câu giờ vô nghĩa kiểu "Bạn có bao giờ tự hỏi...", "Sự thật tiếp theo sẽ khiến bạn sốc...", "Họ luôn giấu bạn điều này...". Mỗi câu thoại (9-13 từ) PHẢI chứa ít nhất 1 dữ kiện thực tế, cơ chế hoạt động ("tại sao/như thế nào"), tên hiện tượng/thí nghiệm/địa danh cụ thể, hoặc hành vi tâm lý sắc bén.
   - Đánh trúng 1 trong 4 cảm xúc cực mạnh của số đông: Nỗi đau/sự bất công thầm kín, nỗi sợ bị tụt hậu (FOMO), khát khao khẳng định giá trị bản thân, hoặc sự tò mò chấn động trước cơ chế tự nhiên/lịch sử có thật.
   - BẮT BUỘC LỒNG GHÉP ÍT NHẤT 1-2 CON SỐ ĐO LƯỜNG XÁC THỰC:
     * Ví dụ: "Gia tốc cú đấm đạt 10,000G làm sôi nước xung quanh...", "Ở độ sâu 10,994m dưới rãnh Mariana...", "Sải cánh dài tới 2.84 mét...", "93% giao tiếp bị lộ qua vi biểu cảm trong 0.2 giây đầu...".
   - Cấu trúc kể chuyện lôi cuốn (Narrative Arc): Mở đầu bằng nghịch lý/dữ kiện chấn động -> Giải thích cơ chế khoa học/lịch sử/tâm lý đằng sau -> Cú ngoặt bất ngờ (Twist) -> Kết nối vòng lặp vô tận (Infinite Loop).
   - KHÔNG cắt vụn cụm danh từ chủ ngữ giữa 2 cảnh (mỗi cảnh phải là một mệnh đề hoàn chỉnh để hệ thống tìm kiếm hình ảnh nhận diện chính xác chủ thể).

11. TỐI ƯU 50% THỜI LƯỢNG ĐẦU & NHỊP BIÊN TẬP NHANH (THE 50% FRONTLOAD RULE):
   - 50% thời lượng đầu (Scenes 1 đến 3) quyết định 90% sự thành bại của video Shorts.
   - Cảnh 1 đóng vai trò như một đoạn Micro-Trailer giật gân, hé lộ ngay hình ảnh hoặc dữ kiện kịch tính nhất của video.
   - Nhịp biên tập (Pacing) của các cảnh trong 50% đầu phải cực nhanh: mỗi cảnh chỉ từ 2.2 - 3.4 giây.

12. TỐI ƯU ẢNH BÌA THỊ TRƯỜNG 1 GIÂY (THUMBNAIL HOOK TEXT):
   - Trường "thumbnail_text" TRẢ VỀ DUY NHẤT 2-4 TỪ IN HOA CỰC TO, GIẬT GÂN, TƯƠNG PHẢN CAO.
   - Không lặp lại nguyên văn tiêu đề mà khơi gợi câu hỏi chưa có lời đáp (Ví dụ: 'SỰ THẬT TÀN KHỐC!', 'CÚ ĐẤM 10,000G!', '0.01% BIẾT ĐIỀU NÀY', 'BÍ MẬT ĐÁY VỰC').

13. CHUẨN HÓA TRUY VẤN GOOGLE IMAGES CHO TỪNG CẢNH (`visual_keyword` & `stock_query`):
   - `visual_keyword` được dùng trực tiếp để truy vấn Google Images & Wikipedia như thanh tìm kiếm thông minh của Gemini:
     * BẮT BUỘC viết bằng tiếng Anh (4-7 từ), chứa TÊN CHÍNH XÁC của chủ thể/loài/nhân vật/hiện tượng + ĐẶC ĐIỂM HÌNH ẢNH CỤ THỂ được nhắc đến trong đúng câu thoại đó (vd: `"peacock mantis shrimp compound eyes macro"`, `"wedge-tailed eagle talons hunting kangaroo"`, `"Antikythera mechanism bronze gears artifact"`).
     * TUYỆT ĐỐI KHÔNG lặp lại cùng một `visual_keyword` hoặc `stock_query` ở các cảnh liền kề; mỗi cảnh phải khai thác một góc chụp/chi tiết khác nhau của chủ đề.

═══════════════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no markdown, no extra text:
═══════════════════════════════════════════════════════════
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "primary_entity": "Full official name of main person/figure/species or null",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "Complete spoken text without stage directions (strictly 85-125 words, 35-46s duration with deep factual payoff and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-4 TỪ IN HOA CỰC TO GỢI TÒ MÒ CHO ẢNH BÌA",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Tư duy logic đạo diễn: Phân tích mâu thuẫn kịch bản và lý giải cách chuyển hóa thành hành động/hình ảnh tương tác cụ thể",
      "narration": "Spoken sentence for this scene (Scene 1 strictly 5-9 words; body scenes 7-11 words, 9 to 12 rapid scenes total)",
      "visual_keyword": "exact entity + specific scene detail for Google Images search (4-7 English words)",
      "visual_prompt": "Cinematic visual description: subject + camera angle + lighting + background",
      "stock_query": "concrete physical nouns + action for 9:16 stock video (3-5 English words)",
      "media_type": "video | ai_image | photo | documentary",
      "estimated_seconds": 3.2
    }}
  ],
  "tags": ["shorts", "viral"],
  "description": "Engaging description with hashtags",
  "pinned_comment": "Irresistible open-ended bait question in {target_lang_display} posted by channel owner to pin at top of comments",
  "viral_formula": "Seamless Infinite Loop | Micro-Hook Escalation | Pattern Interrupt"
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
                script = self._parse_json_script(raw_text)
                last_script = script

                linter_res = ScriptValidationLinter.lint(
                    script=script,
                    niche_profile=niche_profile,
                    cooldown_entities=cooldown_entities,
                    target_wpm=niche_profile.get("target_wpm", 155)
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
                if attempt == max_attempts and not last_script:
                    raise

        logger.error(f"❌ [ContentAgent] Script failed Quality Gate Linter after {max_attempts} attempts: {last_issues}")
        if last_script:
            return last_script
        raise RuntimeError(f"Script validation failed after {max_attempts} attempts: {'; '.join(last_issues)}")

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
You are an elite viral YouTube Shorts director and cinematographer.
We have an existing script, and our Quality Critic / Creator provided specific surgical revisions:

CRITIQUE & REVISION FEEDBACK:
"{feedback}"
{director_block}
CURRENT SCRIPT:
{current_script.model_dump_json(indent=2)}

SURGICALLY REFINE the script strictly addressing the feedback above while preserving the strengths, core entity, and narrative soul of the existing script.
Target Language: {language} ({target_lang_display})
{lang_instruction}

HARD RETENTION RULES:
1. SCENE 1 PATTERN INTERRUPT & PRIMAL VISUAL HOOK (CRITICAL TO PREVENT SWIPING):
   - Scene 1 spoken text must use an urgent warning, elite interrogation tactic, dark law, or counter-intuitive shock (5-9 words strictly, ~2.5s).
   - Scene 1 visual_prompt MUST be a high-tension Primal Visual Hook deduced dynamically from the topic (e.g. scale shock, magnetic attraction, psychological threat, catastrophe). Format: "[Subject + action], [camera angle], [lighting], [environment]"
   - Scene 1 visual_keyword & stock_query MUST be concrete physical nouns (2-3 words).
2. Rapid 35-46s duration with High-Value Payoff: strictly 85-125 words total across 9-12 rapid scenes (2.8-3.8s each, 7-11 words/scene), delivering a deeply satisfying resolution and actionable takeaway.
3. NO ABSTRACT stock_query: 2-3 English CONCRETE PHYSICAL NOUNS only for Pexels.
   NO "psychology", "minimalism", "authority", "power". Use physical objects/actions only.
4. SEAMLESS INFINITE LOOP BRIDGE (ZERO SPOKEN SUBSCRIBE CTA): The final scene MUST deliver the climactic payoff/revelation and bridge seamlessly back to Scene 1's opening hook. STRICTLY FORBIDDEN: NEVER include spoken "Subscribe", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in the voiceover narration! NEVER end with dangling prepositions or conjunctions like 'because', 'which', 'and', 'but', etc.
5. NO LITERAL METAPHORS in visual_prompt: show the actual phenomenon, not metaphorical words.
6. NO DUPLICATE SCENES: every visual_prompt must describe a visually distinct subject and setting.
7. LANGUAGE INTEGRITY (100% STRICT):
   - The entire revised script (title, hook, full_narration, thumbnail_text, and scene narration) MUST be 100% in {target_lang_display}.
   - Even if the current script or feedback is in another language, TRANSLATE and output strictly in {target_lang_display}!
8. Return ONLY valid JSON:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "primary_entity": "Full official name of main person/figure or null",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without any stage directions (strictly 85-125 words for 35-46s with deep payoff and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-4 TỪ IN HOA CỰC TO GỢI TÒ MÒ CHO ẢNH BÌA",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Tư duy logic đạo diễn cho cảnh này",
      "narration": "Text spoken in this scene (7-11 words, 9-12 rapid scenes total)",
      "visual_keyword": "concrete search keywords",
      "visual_prompt": "Detailed cinematic scene: subject + camera angle + lighting + environment",
      "stock_query": "concrete physical nouns",
      "media_type": "video | ai_image | photo | documentary",
      "estimated_seconds": 3.2
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags",
  "pinned_comment": "Provocative engagement bait question for pinned comment in {target_lang_display}",
  "viral_formula": "Seamless Infinite Loop | Micro-Hook Escalation | Pattern Interrupt"
}}
"""
        raw_text = self._call_api(prompt, json_mode=False, timeout=30)
        return self._parse_json_script(raw_text)

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
        return self._parse_json_script(raw_text)

    def generate_best_script(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        min_acceptable_score: float = 8.0,
        max_attempts: int = 3,
    ) -> tuple:
        """
        Quy trình biên soạn cao cấp: Tạo kịch bản -> Tự động chấm điểm bằng ScriptCritic.
        Nếu điểm tệ (< min_acceptable_score hoặc chưa passed), hệ thống tự động tái tạo & tinh chỉnh
        qua nhiều vòng lặp cho đến khi đạt chuẩn viral mới xuất ra cho người dùng.
        Trả về tuple: (ScriptContract, ScriptEvaluationReport).
        """
        from app.graph.evaluators.script_critic import ScriptCritic
        critic = ScriptCritic()

        # 1. Tạo kịch bản sơ khởi
        best_script = self.generate_script(
            topic=topic,
            channel_niche=channel_niche,
            language=language,
            director_notes=director_notes
        )

        try:
            best_eval = critic.evaluate_script(
                script_data=best_script.model_dump(),
                topic=topic,
                channel_style=channel_niche,
                primary_entity=getattr(best_script, "primary_entity", None)
            )
        except Exception as e:
            logger.warning(f"Lỗi khi chấm điểm kịch bản sơ bộ: {e}")
            return best_script, None

        # Nếu ngay vòng 1 đã đạt chuẩn xuất sắc (>= min_acceptable_score và passed) -> Trả về ngay
        if best_eval.overall_score >= min_acceptable_score and best_eval.passed:
            logger.info(f"✅ [ContentAgent] Kịch bản đạt chuẩn ngay vòng 1: {best_eval.overall_score:.1f}/10")
            return best_script, best_eval

        # 2. Vòng lặp tự động tái tạo & hoàn thiện cho đến khi đạt điểm chuẩn
        logger.info(
            f"⚠️ [ContentAgent] Kịch bản sơ bộ chưa đạt ({best_eval.overall_score:.1f}/10 < {min_acceptable_score}). "
            f"Bắt đầu quy trình tự động tái tạo đến khi đạt chuẩn (Tối đa {max_attempts} lần thử)..."
        )

        for attempt in range(1, max_attempts + 1):
            critiques_list = list(best_eval.actionable_critiques) if best_eval.actionable_critiques else []

            # Thêm chỉ đạo chuyên sâu theo các tiêu chí bị điểm kém
            if best_eval.hook_strength.score < 8.0:
                critiques_list.insert(0, f"Cảnh 1 Hook bị trừ nặng ({best_eval.hook_strength.score:.1f}/10): {best_eval.hook_strength.feedback}. Bắt buộc thay thế bằng hook giật gân, đảo ngược định kiến hoặc cảnh báo cấp bách trong 5-8 từ.")
            if best_eval.story_pacing.score < 8.0:
                critiques_list.append(f"Nhịp điệu chưa chuẩn ({best_eval.story_pacing.score:.1f}/10): {best_eval.story_pacing.feedback}. Điều chỉnh lời thoại strictly 80-115 từ cho toàn bộ video, 3.0-4.5s/cảnh, 8-10 cảnh.")
            if getattr(best_eval, "visual_direction", None) and best_eval.visual_direction.score < 8.0:
                critiques_list.append(f"Thị giác yếu ({best_eval.visual_direction.score:.1f}/10): Cấm từ ngữ trừu tượng. visual_keyword và stock_query phải là danh từ/hành động vật lý cụ thể.")

            target_lang_note = (
                "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."
                if not str(language).lower().startswith("vi")
                else "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
            )
            directive = (
                f"KỊCH BẢN TRƯỚC ĐÓ ĐẠT ĐIỂM KÉM ({best_eval.overall_score:.1f}/10). {target_lang_note} "
                f"CHỈ ĐẠO BẮT BUỘC SỬA ĐỂ ĐẠT CHUẨN VIRAL: " + " | ".join(critiques_list)
            )

            try:
                if attempt < max_attempts:
                    # Tinh chỉnh sâu kịch bản hiện tại với các chỉ đạo sửa lỗi
                    candidate_script = self.refine_script(
                        current_script=best_script,
                        feedback=directive,
                        channel_niche=channel_niche,
                        language=language
                    )
                else:
                    # Lần thử cuối cùng: Tái tạo kịch bản từ đầu với góc nhìn sốc và sắc bén hơn
                    refreshed_topic = f"{topic} (Shocking perspective, intense curiosity loop)"
                    candidate_script = self.generate_script(
                        topic=refreshed_topic,
                        channel_niche=channel_niche,
                        language=language,
                        director_notes=f"MUST FIX: {'; '.join(critiques_list[:3])}"
                    )

                candidate_eval = critic.evaluate_script(
                    script_data=candidate_script.model_dump(),
                    topic=topic,
                    channel_style=channel_niche,
                    primary_entity=getattr(candidate_script, "primary_entity", None)
                )

                logger.info(
                    f"🔄 [ContentAgent Auto-Retry {attempt}/{max_attempts}] "
                    f"Điểm số mới: {candidate_eval.overall_score:.1f}/10 (Cũ: {best_eval.overall_score:.1f}/10)"
                )

                # Cập nhật ứng viên tốt nhất
                if candidate_eval.overall_score > best_eval.overall_score:
                    best_script = candidate_script
                    best_eval = candidate_eval

                # Kiểm tra nếu đã vượt qua ngưỡng chuẩn
                if candidate_eval.overall_score >= min_acceptable_score and candidate_eval.passed:
                    logger.info(f"🎉 [ContentAgent] Kịch bản đã đạt chuẩn xuất sắc {candidate_eval.overall_score:.1f}/10 ở lần thử {attempt}!")
                    return candidate_script, candidate_eval

            except Exception as e:
                logger.warning(f"Lỗi trong vòng lặp tự sửa kịch bản lần {attempt}: {e}")
                continue

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

        target_lang_note = (
            "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."
            if not str(language).lower().startswith("vi")
            else "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
        )

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
        channel_id: str = "channel_maya",
        channel_name: str = "Psychology of the world",
        channel_niche: str = "psychology",
        language: str = "en",
        recent_titles: Optional[List[str]] = None,
        count: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Sinh danh sách các chủ đề Shorts triệu view bám sát nội dung thực tế của kênh:
        - Đọc danh sách các video đã đăng/sản xuất của kênh để tránh trùng lặp.
        - Nhân bản công thức chiến thắng từ video đạt 160% Retention (3 Signs They're JUST Using You).
        - Xây dựng cụm chủ đề liên hoàn (Topic Cluster / Binge-watching Playlist) kích thích xem tiếp.
        """
        is_psychology = "psychology" in channel_niche.lower() or "tâm lý" in channel_niche.lower()

        recent_context_block = ""
        if recent_titles:
            clean_titles = [f"  - \"{t}\"" for t in recent_titles[:8] if t]
            if clean_titles:
                recent_context_block = f"""
═══════════════════════════════════════════════════════════
📺 CHANNEL IDENTITY & RECENT VIDEO PORTFOLIO:
Channel Name: '{channel_name}'
Channel Niche: '{channel_niche}'
Recently Produced / Uploaded Videos on this Channel:
{chr(10).join(clean_titles)}

🏆 CHANNEL'S PROVEN WINNING HERO (160.2% RETENTION BENCHMARK):
- "3 Signs They’re JUST Using You 🚩 #psychology" (Viral breakthrough hit!)
- Winning Mechanism: Interpersonal red flags, covert manipulation, spotting exploitation, psychological self-defense.

CRITICAL CHANNEL ALIGNMENT & BINGE-WATCHING RULES:
1. DEEP CHANNEL SYNERGY: The {count} suggested concepts MUST feel like natural sister episodes in a binge-worthy playlist for '{channel_name}'.
2. NO DUPLICATES: DO NOT suggest topics or titles already covered in the recent videos list above!
3. BINGE-WATCHING MULTIPLIER: Viewers who watched "3 Signs They’re JUST Using You" must feel an irresistible psychological compulsion to click these videos next (e.g. Secret jealousy in friend circles, subtle tests toxic people use, psychological boundary defense, detecting hidden deceit).
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

