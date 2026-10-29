"""app.agents.content.script_json_parser_mixin — Gọi Gemini REST API xoay vòng khóa & bóc tách/chuẩn hóa JSON kịch bản."""
import json
import logging
import requests
import time
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
from app.utils.langsmith_tracer import trace_custom_span
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)



class ScriptJsonParserMixin:
    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self.session = requests.Session()

    @trace_custom_span("ContentAgent.gemini_generate", run_type="llm")
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
            if json_mode and not model.startswith("gemma-"):
                payload["generationConfig"] = KeyRotator.build_fast_json_gen_config(model, temperature=0.35)
            elif not model.startswith("gemma-") and "lite" not in model.lower():
                payload["generationConfig"] = {"temperature": 0.4, "thinkingConfig": {"thinkingBudget": 0}}

            try:
                attempts += 1
                rem_budget = max(8.0, float(max_total_seconds) - elapsed)
                req_timeout = min(timeout, 12 if model.startswith("gemma-") else 14, max(8, int(rem_budget * 0.55)))
                r = self.session.post(url, json=payload, timeout=(3.5, req_timeout))
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        texts = [
                            p.get("text", "")
                            for p in parts
                            if isinstance(p, dict) and "text" in p and not p.get("thought")
                        ]
                        full_text = "".join(texts).strip()
                        if full_text:
                            logger.info(f"✅ Gemini API ({model}) thành công với key {key[:8]}... trong {time.monotonic()-start_time:.1f}s")
                            return full_text
                elif r.status_code in (429, 403, 404, 500, 502, 503, 504):
                    err_txt = r.text[:400]
                    logger.warning(f"⚠️ ({model}) trên key {key[:8]}... gặp mã {r.status_code}. Ngắt mạch 60s & chuyển sang Model khỏe mạnh kế tiếp...")
                    self.rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=err_txt)
                    continue
                else:
                    err_msg = ""
                    try:
                        err_msg = r.json().get("error", {}).get("message", "")[:80]
                    except Exception:
                        err_msg = r.text[:80]
                    logger.warning(f"Gemini API ({model}) trả lỗi {r.status_code} với key {key[:8]}...: {err_msg}")
                    self.rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=err_msg)
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
        topic: str = "", video_format: str = "short", **kwargs,
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
        if "video_format" not in raw_data or not str(raw_data.get("video_format") or "").strip():
            raw_data["video_format"] = str(video_format or "short").strip().lower()

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

            # Nếu là ngách Tâm lý học con người (psychology), khử bẫy ẩn dụ động vật ('wolf teeth', 'forest predator', 'lion')
            if str(channel_niche or "").strip().lower() == "psychology":
                _animal_metaphor_re = re.compile(
                    r"\b(?:wolf|wolves|lion|lions|tiger|tigers|shark|sharks|snake|snakes|caiman|crocodile|"
                    r"baring\s+teeth|wolf\s+teeth|forest\s+predator|jungle\s+predator)\b",
                    re.IGNORECASE,
                )
                if _animal_metaphor_re.search(vk):
                    vk = "confident man cold gaze noir"
                if _animal_metaphor_re.search(sq):
                    sq = "confident man intense gaze"

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
                # Nếu LLM vô tình lặp lại tiêu đề dạng "Thực thể A vs Thực thể B: [Nội dung câu Hook]" ở đầu Cảnh 1,
                # tự động gọt bỏ phần nhãn tiêu đề trước dấu hai chấm nếu vế sau đủ >= 5 từ hoàn chỉnh:
                m_title_echo_colon = re.match(
                    r'^[^.!?]{2,42}\b(?:vs\.?|versus|contra)\b[^.!?]{2,42}:\s*(.+)$',
                    cleaned_h1,
                    flags=re.IGNORECASE | re.UNICODE,
                )
                if m_title_echo_colon and len(m_title_echo_colon.group(1).strip().split()) >= 5:
                    cleaned_h1 = m_title_echo_colon.group(1).strip()
                if cleaned_h1 and len(cleaned_h1.split()) >= 3:
                    if cleaned_h1[0].islower():
                        cleaned_h1 = cleaned_h1[0].upper() + cleaned_h1[1:]
                    scenes[0]["narration"] = cleaned_h1
                    raw_data["hook"] = cleaned_h1

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

            # Riêng với Cảnh 1 (prefer_word_idx <= 7) nếu câu ghép dài (>= 10 từ) có dấu phẩy / gạch ngang / liên từ
            # (", yet ", ", but ", ", mas ", " — ", " - ") -> Tách gọn để Cảnh 1 luôn dứt điểm trong 5-8 từ (1.6s-2.2s)!
            if prefer_word_idx <= 7 and len(words) >= 10:
                import re as _re
                m_conj = _re.search(
                    r'^([^,;\—\-]{15,65})(?:[,;\—\-]+\s*(?:yet|but|and|while|until|mas|porém|contudo|enquanto|até)\s+|\s+[\—\-]+\s+)(.{14,})$',
                    text_clean,
                    flags=_re.IGNORECASE,
                )
                if m_conj:
                    l_part = m_conj.group(1).strip().rstrip(",;.-—") + "!"
                    r_part = m_conj.group(2).strip()
                    if r_part and r_part[0].islower():
                        r_part = r_part[0].upper() + r_part[1:]
                    if 4 <= len(l_part.split()) <= 9 and len(r_part.split()) >= 4:
                        return l_part, r_part
            return None

        if scenes and isinstance(scenes, list):
            is_long_form_parse = str(raw_data.get("video_format") or video_format or "short").strip().lower() == "long"
            # Nếu LLM tách nhầm phần dẫn câu thoại ("...by saying out loud.", "...and ask.") và câu thoại trực tiếp sang 2 cảnh liền kề -> Gộp lại thành 1 cảnh hoàn chỉnh!
            _speech_intro_tail_re = re.compile(
                r'(?:\bby\s+saying(?:\s+out\s+loud)?|\band\s+(?:ask|say|whisper|reply|tell\s+them)|\brespond\s+with|\bsimply\s+(?:ask|say)|'
                r'\bdizendo(?:\s+em\s+voz\s+alta)?|\be\s+(?:pergunte|diga|responda))[\.\:\!\,\s]*$',
                re.IGNORECASE,
            )
            merged_pre_scenes: List[Dict[str, Any]] = []
            i_m = 0
            while i_m < len(scenes):
                cur_s = scenes[i_m]
                if (
                    isinstance(cur_s, dict)
                    and i_m + 1 < len(scenes)
                    and isinstance(scenes[i_m + 1], dict)
                    and _speech_intro_tail_re.search(str(cur_s.get("narration") or "").strip())
                ):
                    nxt_s = scenes[i_m + 1]
                    intro_txt = str(cur_s.get("narration") or "").strip().rstrip(".,!:")
                    quote_txt = str(nxt_s.get("narration") or "").strip().strip('"\'')
                    combined_s = dict(cur_s)
                    combined_s["narration"] = f'{intro_txt}: "{quote_txt}"'
                    if not combined_s.get("overlay_badge") and nxt_s.get("overlay_badge"):
                        combined_s["overlay_badge"] = nxt_s.get("overlay_badge")
                    merged_pre_scenes.append(combined_s)
                    i_m += 2
                else:
                    if isinstance(cur_s, dict):
                        merged_pre_scenes.append(cur_s)
                    i_m += 1
            scenes = merged_pre_scenes

            paced_scenes: List[Dict[str, Any]] = []
            for idx_sc, sc in enumerate(scenes):
                if not isinstance(sc, dict):
                    continue
                narr_str = str(sc.get("narration") or "").strip()
                w_list = narr_str.split()
                w_count = len(w_list)

                # Chỉ tách Cảnh 1 nếu >= 12 từ, hoặc tách cảnh thân khi tổng số cảnh < 7 (không bao giờ tách cảnh đang có câu thoại trong ngoặc kép!)
                has_quote_or_colon = ('"' in narr_str or "'" in narr_str or ":" in narr_str)
                should_split = (
                    not is_long_form_parse
                    and (
                        (idx_sc == 0 and w_count >= 12)
                        or (idx_sc > 0 and w_count >= 25 and len(scenes) < 7 and not has_quote_or_colon)
                    )
                )
                target_pref = 6 if idx_sc == 0 else max(8, w_count // 2)
                split_res = _split_long_narration(narr_str, target_pref) if should_split else None

                if split_res and not _speech_intro_tail_re.search(split_res[0]):
                    left_narr, right_narr = split_res
                    sc_first = dict(sc)
                    sc_first["narration"] = left_narr
                    min_s1_dur = 1.8 if idx_sc == 0 else 2.5
                    sc_first["estimated_seconds"] = round(max(min_s1_dur, len(left_narr.split()) / 2.8), 1)
                    sc_first["highlight_words"] = _auto_extract_scene_highlights(left_narr)

                    sc_second = dict(sc)
                    sc_second["narration"] = right_narr
                    sc_second["overlay_badge"] = None
                    sc_second["highlight_words"] = _auto_extract_scene_highlights(right_narr)
                    base_vk = str(sc.get("visual_keyword") or "cinematic scene").strip()
                    base_sq = str(sc.get("stock_query") or base_vk).strip()
                    base_vp = str(sc.get("visual_prompt") or f"Cinematic 8k vertical shot of {base_vk}").strip()
                    sc_second["visual_keyword"] = base_sq if base_sq != base_vk else f"{base_vk} close up"
                    sc_second["stock_query"] = base_sq
                    sc_second["visual_prompt"] = base_vp
                    sc_second["estimated_seconds"] = round(max(2.4, len(right_narr.split()) / 2.65), 1)

                    paced_scenes.append(sc_first)
                    paced_scenes.append(sc_second)
                else:
                    min_dur = 1.9 if idx_sc == 0 else 2.5
                    max_est_dur = 22.0 if is_long_form_parse else 5.4
                    wps_div = 2.45 if is_long_form_parse else 2.75
                    sc["estimated_seconds"] = round(max(min_dur, min(max_est_dur, w_count / wps_div)), 1)
                    paced_scenes.append(sc)

            # Đánh lại số thứ tự scene_id chuẩn xác 1..N và làm giàu highlight_words / camera_motion / sfx_cue
            _motion_cycle = ["zoom_in_fast", "ken_burns_zoom_out", "pan_right", "ken_burns_zoom_in", "pan_left"]
            def _direct_scene_motion_and_prompt(sc_item: Dict[str, Any], idx_1based: int, default_m: str) -> None:
                narr_txt = str(sc_item.get("narration") or "").lower()
                vp_txt = str(sc_item.get("visual_prompt") or sc_item.get("visual_keyword") or "").strip()
                vp_low = vp_txt.lower()
                ctx_s = f"{narr_txt} {vp_low}"

                # Đạo diễn góc máy theo ngữ cảnh (Prompt #2 & #4: tiến gần, lia nhẹ, hoặc đổi tiêu điểm)
                cur_m = str(sc_item.get("camera_motion") or "").strip().lower()
                if idx_1based == 1:
                    sc_item["camera_motion"] = "hyper_zoom"
                elif not cur_m or cur_m == "auto":
                    if any(w in ctx_s for w in ("eyes", "gaze", "face", "whisper", "secret", "shock", "paradox", "ánh mắt", "khuôn mặt", "bí mật")):
                        sc_item["camera_motion"] = "zoom_in"
                    elif any(w in ctx_s for w in ("vast", "ocean", "trench", "abyss", "empire", "horizon", "world", "đại dương", "toàn cảnh")):
                        sc_item["camera_motion"] = "zoom_out"
                    elif any(w in ctx_s for w in ("tower", "rise", "surface", "sky", "canopy", "tall", "vươn lên", "bầu trời")):
                        sc_item["camera_motion"] = "pan_up"
                    elif any(w in ctx_s for w in ("chase", "run", "hunt", "across", "follow", "stalk", "rượt đuổi", "băng qua")):
                        sc_item["camera_motion"] = "pan_right" if idx_1based % 2 == 0 else "pan_left"
                    else:
                        sc_item["camera_motion"] = default_m

                # Bổ sung lớp vi chuyển động vật lý & khóa chống méo hình cho visual_prompt (Prompt #1, #4, #5)
                if vp_txt and "anatomical" not in vp_low and "micro-physics" not in vp_low:
                    sc_item["visual_prompt"] = (
                        f"{vp_txt}, natural micro-physics of hair, fabric, and ambient light, "
                        f"shallow depth-of-field, stable background geometry, exact facial and anatomical fidelity"
                    ).strip(", ")
            for new_id, p_sc in enumerate(paced_scenes, start=1):
                p_sc["scene_id"] = new_id
                if not p_sc.get("highlight_words"):
                    p_sc["highlight_words"] = _auto_extract_scene_highlights(str(p_sc.get("narration") or ""))
                if new_id == 1:
                    p_sc["camera_motion"] = "hyper_zoom"
                    p_sc["sfx_cue"] = "none"
                else:
                    if not p_sc.get("camera_motion"):
                        pass
                    if not p_sc.get("sfx_cue"):
                        p_sc["sfx_cue"] = "none"
                _direct_scene_motion_and_prompt(p_sc, new_id, _motion_cycle[(new_id - 1) % len(_motion_cycle)])
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
                r'[\s\.\,\-\—\…]+(?:because|which|and|but|or|while|although|though|unless|whereas|'
                r'the|a|an|his|her|their|its|my|your|our|these|those|'
                r'porque|pois|mas|embora|enquanto|que|'
                r'bởi vì|nhưng|hoặc|mặc dù)[\s\.\,\-\—\…]*$',
                re.IGNORECASE,
            )
            _academic_opener_re = re.compile(
                r'^(?:(?:[a-zA-ZÀ-ỹ\-]+\s+){0,4}(?:studies|research|scientists|researchers|data|experiments|'
                r'estudos|pesquisas|cientistas|experimentos)(?:\s+de\s+[a-zA-ZÀ-ỹ\-]+(?:\s+[a-zA-ZÀ-ỹ\-]+)?)?\s+'
                r'(?:show|shows|reveal|reveals|indicate|indicates|prove|proves|confirm|confirms|found|demonstrate|demonstrates|'
                r'mostram|revelam|indicam|provam|confirmam|demonstram)\s+(?:that|que)\s+)(.{24,})$',
                re.IGNORECASE,
            )
            for idx_s, sc_obj in enumerate(scenes):
                if not isinstance(sc_obj, dict):
                    continue
                s_narr = str(sc_obj.get("narration") or "").strip()
                s_narr = _stage_tag_re.sub("", s_narr)
                s_narr = re.sub(r'\s+', ' ', s_narr).strip()
                m_acad = _academic_opener_re.match(s_narr)
                if m_acad and len(m_acad.group(1).strip().split()) >= 6:
                    s_narr = m_acad.group(1).strip()
                    if s_narr and s_narr[0].islower():
                        s_narr = s_narr[0].upper() + s_narr[1:]
                # Chỉ khử các liên từ phụ thuộc / mạo từ / tính từ sở hữu thực sự bị bỏ lửng ở cuối câu
                # (TUYỆT ĐỐI KHÔNG cắt các từ hợp lệ ở cuối câu tiếng Anh như 'no', 'do', 'in', 'on', 'to', 'is', 'are', 'why')
                while _dangling_end_re.search(s_narr):
                    s_narr = _dangling_end_re.sub("", s_narr).strip()
                s_narr = s_narr.rstrip(",-—…")
                if s_narr and not s_narr.endswith((".", "!", "?", '"', "'", "”")):
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
                _hook_stop = {
                    "the", "and", "for", "with", "from", "into", "this", "that", "how", "why", "what", "when", "who",
                    "uma", "uns", "umas", "dos", "das", "nos", "nas", "por", "para", "com", "sem", "sob", "sobre",
                    "que", "como", "mais", "muito", "este", "esta", "esse", "essa", "isso", "pelo", "pela",
                    "shorts", "short", "video", "vs", "versus", "contra", "mordida", "letal", "duelo", "ataque", "psi",
                }
                _raw_t_clean = re.sub(r"#\w+", " ", str(raw_data.get("title") or "")).strip()
                _title_ent_tokens = {
                    w.lower()
                    for w in re.findall(r"\b[a-zA-ZÀ-ỹ]{4,}\b", _raw_t_clean, flags=re.UNICODE)
                    if w.lower() not in _hook_stop
                }

                def _score_hook_retention(h_str: str) -> float:
                    h_score = 50.0
                    words_h = re.findall(r'\b\w+\b', h_str)
                    w_len = len(words_h)
                    h_toks = {w.lower() for w in re.findall(r"\b[a-zA-ZÀ-ỹ]{4,}\b", h_str, flags=re.UNICODE)}
                    # Bắt buộc ưu tiên Hook nêu đích danh thực thể chính từ Tiêu đề (chống Entity-Blind Hook bị phạt 5.8 điểm)
                    if _title_ent_tokens:
                        if _title_ent_tokens & h_toks:
                            h_score += 28.0
                        else:
                            h_score -= 45.0
                    # Độ dài vàng cho Hook 0.0s từ 343 Shorts triệu view (5 - 8 từ ~ 1.6s - 2.2s): Dứt điểm trước phản xạ vuốt 1.8s!
                    if 5 <= w_len <= 8:
                        h_score += 26.0
                    elif 9 <= w_len <= 10:
                        h_score += 16.0
                    elif w_len > 11 or w_len < 4:
                        h_score -= 22.0
                    # Chứa số liệu / tốc độ / lực cắn / thời gian cụ thể (chữ số hoặc số viết bằng chữ)
                    if re.search(
                        r'(?:\d|\b(?:one|two|three|four|five|ten|forty|fifty|hundred|thousand|million|percent|milliseconds|seconds|psi|degrees|'
                        r'um|uma|dois|duas|três|quatro|cinco|dez|cem|mil|milissegundos|segundos|quilos|metros)\b)',
                        h_str,
                        flags=re.IGNORECASE,
                    ):
                        h_score += 28.0
                    # Phạt các câu chỉ trỏ mơ hồ giấu tên chủ thể ('Look closely at...', 'Watch the exact moment...')
                    if re.match(r'^(?:look\s+(?:closely\s+)?at|watch\s+(?:the\s+exact\s+moment|how|why)|olhe\s+bem\s+para)\b', h_str.strip(), flags=re.IGNORECASE):
                        h_score -= 30.0
                    # Thưởng cho cấu trúc nêu đích danh chủ thể / tình huống đối thoại trực diện
                    if re.search(
                        r'\b(?:this man|this woman|this dog|this puppy|this jaguar|if someone says|when someone|turn any|never answer|'
                        r'esse|essa|esses|essas|este|esta|nem o|nem a|até o|até a|uma única|um único)\b',
                        h_str,
                        flags=re.IGNORECASE,
                    ):
                        h_score += 14.0
                    # Thưởng lớn cho công thức "A vs B" / Đối đầu bất đối xứng & Nghịch lý sợ hãi (pt-BR & Wild Odyssey Formula)
                    if re.search(r'\b(?:vs\.?|versus|against|contra|outsmart|crushes|destroys|beats|backs down|perfura|esmaga|esmagam|paralisa|recua|recuam|aterroriza|teme|temem|pânico)\b', h_str, flags=re.IGNORECASE):
                        h_score += 18.0
                    # Phạt nếu mở đầu kiểu nhãn từ điển khô khan ("Lagosta-Boxeadora: um soco...") thiếu động từ đầu câu
                    if re.match(r'^[A-Za-zÀ-ỹçãõáéíóúâêô\-\s]{2,26}:\s', h_str.strip()):
                        h_score -= 20.0
                    # Chứa động từ hành động mạnh / nghịch lý / sinh tồn (EN / VI / PT)
                    power_hook_words = (
                        "never", "deadly", "crush", "swallow", "silent", "seconds", "freeze", "blind", "strike", "bite",
                        "venom", "paradox", "impossible", "mistake", "alive", "shivering", "refused", "terror", "abyss",
                        "weapon", "trap", "manipulat", "narcissist", "toxic", "lie", "liar", "secret", "warning", "insult", "respect",
                        "tử thần", "ngay lập tức", "nghịch lý", "cắn", "nuốt", "đóng băng", "run rẩy", "tuyệt đối", "bí mật",
                        "jamais", "mortal", "esmagar", "engolir", "silêncio", "segundos", "erro", "impossível", "veneno", "abismo", "ferve"
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


        # ── 2.86. KHỬ NHIỄM NGÔN NGỮ TỰ ĐỘNG (CROSS-LANGUAGE LEAKAGE SANITIZER) ──
        # Chặn tuyệt đối hiện tượng LLM bị rò rỉ câu/từ Tiếng Việt vào Cảnh 1 (Hook), lời thoại hoặc phù hiệu
        # trên các kênh Tiếng Bồ Đào Nha (pt-BR) và Tiếng Anh (en).
        _vn_excl_re = re.compile(r"[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]", re.IGNORECASE)
        if scenes and isinstance(scenes, list):
            _vn_sc_count = sum(
                1 for s in scenes
                if isinstance(s, dict) and _vn_excl_re.search(str(s.get("narration") or ""))
            )
            _is_non_vn_script = _vn_sc_count < max(2, (len(scenes) + 1) // 2)
            if _is_non_vn_script:
                for idx_s, s in enumerate(scenes):
                    if not isinstance(s, dict):
                        continue
                    narr_s = str(s.get("narration") or "")
                    if narr_s:
                        narr_s = re.sub(r"\bxé-to-cando\b", "rasgando", narr_s, flags=re.IGNORECASE)
                        narr_s = re.sub(r"\bva-cham\b", "colisão", narr_s, flags=re.IGNORECASE)
                        s["narration"] = narr_s
                    if idx_s == 0 and _vn_excl_re.search(str(s.get("narration") or "")):
                        # Tìm phương án Hook sạch (không chứa tiếng Việt) từ hook_variants hoặc title
                        clean_hook_repl = ""
                        raw_h_vars = raw_data.get("hook_variants")
                        if isinstance(raw_h_vars, list):
                            for hv in raw_h_vars:
                                hv_c = _strip_variant_prefix(str(hv or "")).strip()
                                if hv_c and not _vn_excl_re.search(hv_c):
                                    clean_hook_repl = hv_c
                                    break
                        if not clean_hook_repl:
                            t_cand = _strip_variant_prefix(str(raw_data.get("title") or "")).strip()
                            if t_cand and not _vn_excl_re.search(t_cand):
                                clean_hook_repl = t_cand
                        if clean_hook_repl:
                            logger.warning(
                                f"[ContentAgent] 🛡️ Phát hiện Cảnh 1 bị rò rỉ Tiếng Việt trên kịch bản ngoại ngữ: "
                                f"'{s.get('narration')}' -> Tự động khử nhiễm bằng: '{clean_hook_repl}'"
                            )
                            s["narration"] = clean_hook_repl
                    badge_s = str(s.get("overlay_badge") or "")
                    if badge_s and (_vn_excl_re.search(badge_s) or "TỪ IN HOA" in badge_s.upper()):
                        s["overlay_badge"] = ""
                    elif badge_s and any(c in str(raw_data.get("title") or "").lower() for c in ("ç", "ã", "õ", "onça", "jacaré", "oceano")):
                        _pt_badge_map = {
                            "FATAL MECHANISM": "MECANISMO FATAL",
                            "THE 15-SECOND TWIST": "A REVIRAVOLTA",
                            "THE REAL TRAP": "A ARMADILHA REAL",
                            "THE FATAL MISTAKE": "O ERRO FATAL",
                            "CLASH": "IMPACTO",
                            "DEADLY DUEL": "DUELO MORTAL",
                        }
                        b_up = badge_s.upper()
                        for en_k, pt_v in _pt_badge_map.items():
                            if en_k in b_up:
                                b_up = b_up.replace(en_k, pt_v)
                        s["overlay_badge"] = b_up

        # Cập nhật lại full_narration & hook để đồng bộ 100% với Cảnh 1 đã tối ưu nhịp
        raw_data["full_narration"] = " ".join(s.get("narration", "") for s in scenes if isinstance(s, dict))
        if scenes and isinstance(scenes[0], dict) and scenes[0].get("narration"):
            raw_data["hook"] = str(scenes[0]["narration"]).strip()
        elif "hook" not in raw_data or not raw_data["hook"]:
            raw_data["hook"] = raw_data.get("title", "")

        # ── 2.88. CỔNG QUYẾT ĐỊNH HOOK CỦA GEMINI: LUÔN ƯU TIÊN VIDEO CHUYỂN ĐỘNG THẬT ("video") CHO CẢNH 1 TRÊN MỌI NGÁCH ──
        raw_h_mode = str(raw_data.get("hook_visual_mode") or "").strip().lower()
        raw_is_abs = raw_data.get("is_abstract_keyword")
        if raw_h_mode in ("video", "short", "shorts", "clip", "scene", "image", "ai_image", "photo", "traditional", ""):
            raw_data["hook_visual_mode"] = "video"
            raw_data["is_abstract_keyword"] = False if raw_is_abs is None else bool(raw_is_abs)
        else:
            raw_data["hook_visual_mode"] = "video"
            raw_data["is_abstract_keyword"] = False

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
            raw_data["hook_search_queries"] = [str(q).strip() for q in raw_h_queries if str(q).strip()][:5]
        elif isinstance(raw_h_queries, str) and raw_h_queries.strip():
            raw_data["hook_search_queries"] = [q.strip() for q in raw_h_queries.split(",") if q.strip()][:5]

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
        if len(candidates) >= 1:
            prim_ent_str = str(raw_data.get("primary_entity") or "").strip()
            prim_tokens = [t.lower() for t in re.findall(r"[a-zA-ZÀ-ỹ]{4,}", prim_ent_str) if t.lower() not in ("animal", "creature", "monster", "predator", "history", "ocean")]
            _vague_title_re = re.compile(
                r"\b(?:this\s+(?:cute\s+|deadly\s+|terrifying\s+|tiny\s+|giant\s+)?(?:creature|animal|beast|place|point|bird|thing|monster|predator)|"
                r"invisible\s+monsters|one\s+bite\s+is\s+all(?:\s+it\s+takes)?|the\s+point\s+in\s+the\s+ocean\s+where|"
                r"este\s+animal|esta\s+criatura|esta\s+ave)\b",
                re.IGNORECASE,
            )

            def _score_title_ctr(t_str: str) -> float:
                score = 50.0
                clean_t = re.sub(r'#\w+', '', _strip_variant_prefix(t_str)).strip()
                c_len = len(clean_t)
                t_low = clean_t.lower()
                # Chiều dài vàng trên Shorts Shelf Mobile: 26 - 56 ký tự (không bị cắt '...')
                if 26 <= c_len <= 56:
                    score += 20.0
                elif c_len > 60:
                    score -= 22.0
                # Chứa con số / dữ kiện định lượng / đối đầu trực diện
                has_num = bool(re.search(r'\d', clean_t))
                if has_num:
                    score += 18.0
                if re.search(r'\b(vs\.?|versus|contra|flee|flees|temem|odeiam|crushes|esmagam|aniquila|terrified|tremem|smile|jump|refused|hay|or)\b', clean_t, flags=re.IGNORECASE):
                    score += 16.0
                if prim_tokens and any(pt in t_low for pt in prim_tokens):
                    score += 15.0
                # Phạt nặng các tiêu đề ẩn dụ mơ hồ giấu tên chủ thể (nguyên nhân làm video kẹt < 100 views)
                if _vague_title_re.search(clean_t) and not (has_num and prim_tokens and any(pt in t_low for pt in prim_tokens)):
                    score -= 32.0
                # Chứa từ khóa kích hoạt tò mò / cảnh báo / giả định (Curiosity Gap & Counterfactual Triggers)
                ctr_triggers = (
                    "what if", "why", "never", "secret", "truth", "sign", "until", "mistake", "hidden", "actually", "refused",
                    "nếu", "tại sao", "đừng", "bí mật", "sự thật", "dấu hiệu", "cho đến khi", "sai lầm", "ẩn giấu",
                    "e se", "por que", "nunca", "segredo", "verdade", "erro", "jamais"
                )
                if any(k in t_low for k in ctr_triggers):
                    score += 14.0
                # Bảo toàn tuyệt đối cấu trúc "What If..." cho kênh Alternate History & "What If?"
                if is_what_if_context and any(w in t_low for w in ("what if", "e se ", "nếu ")):
                    score += 26.0
                if "?" in clean_t or "!" in clean_t:
                    score += 5.0
                return score

            best_title = max(candidates, key=_score_title_ctr)
            if prim_ent_str and _vague_title_re.search(best_title) and not any(pt in best_title.lower() for pt in prim_tokens):
                best_title = re.sub(
                    r"\bthis\s+(?:cute\s+|deadly\s+|terrifying\s+|tiny\s+|giant\s+)?(?:creature|animal|beast|bird|monster|predator)\b",
                    f"the {prim_ent_str}",
                    best_title,
                    count=1,
                    flags=re.IGNORECASE,
                )
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
        comb_all = f"{niche_norm} {str(raw_data.get('topic', ''))} {str(raw_data.get('title', ''))} {str(raw_data.get('full_narration', ''))[:240]}".lower()
        is_pt_lang = (
            "ç" in comb_all or "õ" in comb_all or "ã" in comb_all
            or any(w in comb_all for w in ("você", "não", "por que", "oceano", "selva", "onça", "jacaré", "segredo", "ilha", "caverna", "leões", "ratel", "harpia", "metros", "cachalote"))
        )
        is_vn = bool(_vn_excl_re.search(comb_all)) and not is_pt_lang and not (scenes and _is_non_vn_script)
        raw_thumb_txt = str(raw_data.get("thumbnail_text") or "").strip()
        if (
            not raw_thumb_txt
            or (not is_vn and (_vn_excl_re.search(raw_thumb_txt) or "TỪ IN HOA" in raw_thumb_txt.upper() or "SỰ THẬT" in raw_thumb_txt.upper()))
        ):
            try:
                from app.services.thumbnail_service import ThumbnailService
                derived_thumb = ThumbnailService().derive_curiosity_text(topic=raw_data.get("topic", ""), viral_title=raw_data.get("title", ""))
                if not is_vn and _vn_excl_re.search(str(derived_thumb or "")):
                    derived_thumb = "VERDADE BRUTAL!" if is_pt_lang else "SHOCKING TRUTH!"
                raw_data["thumbnail_text"] = derived_thumb
            except Exception:
                raw_data["thumbnail_text"] = "SỰ THẬT TÀN KHỐC!" if is_vn else ("VERDADE BRUTAL!" if is_pt_lang else "SHOCKING TRUTH!")

        # ── 3. BÌNH LUẬN MỒI GHIM ĐẦU TRANG (PINNED COMMENT BAIT) TỐI ƯU TƯƠNG TÁC ──
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
                is_duel = any(k in comb_all for k in (" vs ", "duelo", "cachalote", "lula-colossal", "lula gigante", "tubarão", "orca", "showdown"))
                if is_pt_lang:
                    raw_data["pinned_comment"] = (
                        "Honestamente: se essa revanche acontecesse no escuro total a 2.000m, o resultado mudaria? (1) Sim ou (2) Sem chance? 👇🦑🐋"
                        if is_duel else
                        "Se oferecessem US$ 1 milhão para você descer até esse lugar por 10 minutos: (1) Aceitaria ou (2) Jamais? 👇🌊"
                    )
                else:
                    raw_data["pinned_comment"] = (
                        "Nếu được trả 1 triệu USD để lặn xuống độ sâu này trong 10 phút: Bạn chọn (1) Nhận lời hay (2) Không bao giờ? 👇"
                        if is_vn else
                        "Be honest: for $1,000,000, would you descend into this zone for 10 minutes? (1) Take the money or (2) Never? 👇🌊"
                    )
            elif niche_norm == "wildlife_predators" or any(k in comb_all for k in ("săn mồi", "predator", "wildlife", "dã thú", "báo", "sư tử", "hổ", "cheetah", "leopard", "eagle", "onça", "sucuri", "jacaré", "ratel", "hippo", "orca")):
                raw_data["pinned_comment"] = (
                    "Honestamente: e se fossem 2 contra 1 ao mesmo tempo nessa disputa, o vencedor ainda sobreviveria? (1) Sim ou (2) Sem chance? 👇🔥"
                    if is_pt_lang else (
                        "Thật lòng đi: nếu kèo này là 2 đánh 1 cùng lúc thì kẻ thắng cuộc còn trụ nổi không? (1) Vẫn thắng hay (2) Không có cửa? 👇🔥"
                        if is_vn else
                        "Be honest: if it were a 2-on-1 ambush at the same time, does the winner still walk away? (1) Yes or (2) No chance? 👇🔥"
                    )
                )
            elif niche_norm == "psychology" or any(k in comb_all for k in ("tâm lý", "psychology", "manipulat", "thao túng", "mindset", "gaslight", "liar", "dark psychology")):
                raw_data["pinned_comment"] = (
                    "Khi phát hiện ai đó dùng chiêu này với bạn, bạn sẽ: (1) Vạch mặt ngay lập tức hay (2) Giả vờ không biết để quan sát? 👇"
                    if is_vn else
                    "When you catch someone using this tactic on you, do you: (1) Call them out immediately or (2) Play along and watch? 👇🧠"
                )
            else:
                raw_data["pinned_comment"] = (
                    "Nếu rơi vào tình huống này ngoài đời thực, bạn chọn (1) Đối mặt hay (2) Rút lui ngay lập tức? 👇"
                    if is_vn else
                    "If you faced this exact situation in real life, would you: (1) Stand your ground or (2) Walk away immediately? 👇"
                )

        # Khử sạch đuôi quảng cáo công nghiệp ("🔔 INSCREVA-SE...", "🔔 SUBSCRIBE...", "🔔 ĐĂNG KÝ...")
        # để bình luận đầu tiên của chủ kênh trông tự nhiên 100% như một cuộc tranh luận 50/50 thật sự.
        cur_pin = str(raw_data.get("pinned_comment") or "").strip()
        if cur_pin:
            cur_pin = re.sub(
                r'\s*(?:🔔|🐾|🌊|🦁)?\s*(?:inscreva-se|subscribe|đăng\s*ký)\b.*$',
                '',
                cur_pin,
                flags=re.IGNORECASE,
            ).strip()
            cur_pin = re.sub(
                r'\s+(?:e\s+inscreva-se(?:\s+no\s+canal)?|and\s+subscribe|&\s*subscribe)\b[^?!.]*',
                '',
                cur_pin,
                flags=re.IGNORECASE,
            ).strip()
            raw_data["pinned_comment"] = cur_pin

        # ── 4. NICHE-AWARE CLEAN SHORTS DESCRIPTION (CHỐNG RÒ RỈ METADATA CHÉO NGÁCH & CHỐNG SPAM BREAKDOWN) ──
        raw_desc = str(raw_data.get("description") or "").strip()
        is_psych_niche = (
            niche_norm == "psychology"
            or (not niche_norm or niche_norm == "general")
            and any(k in comb_all for k in ("psychology", "tâm lý", "manipulat", "gaslight", "narcissist", "liar"))
        )
        if not is_psych_niche and raw_desc:
            raw_desc = re.sub(r'#(?:psychology|darkpsychology|selfmastery|mindcontrol)\b', '', raw_desc, flags=re.IGNORECASE).strip()
            raw_data["description"] = raw_desc

        if len(raw_desc) < 60 and raw_data.get("scenes"):
            sc_list = raw_data.get("scenes") or []
            hook_str = str(raw_data.get("hook") or (sc_list[0].get("narration", "") if sc_list else "")).strip()
            act2_str = str(sc_list[min(1, len(sc_list) - 1)].get("narration", "")).strip() if len(sc_list) > 1 else ""

            if niche_norm == "alternate_history" or any(k in comb_all for k in ("what if", "alternate history", "empire", "roman", "alexandria")):
                default_tags = ["#shorts", "#alternatehistory", "#whatif", "#history"]
            elif niche_norm == "deep_sea_earth_mysteries" or any(k in comb_all for k in ("deep sea", "ocean", "mystery", "caverna", "ilha", "oceano", "abyss")):
                default_tags = (
                    ["#shorts", "#misterios", "#oceano", "#ciencia", "#curiosidades"]
                    if is_pt_lang else
                    ["#shorts", "#mysteries", "#deepsea", "#science", "#unexplained"]
                )
            elif niche_norm == "wildlife_predators" or any(k in comb_all for k in ("predator", "wildlife", "onça", "jacaré", "ratel", "harpia", "hippo", "orca", "leopard", "anaconda")):
                default_tags = (
                    ["#shorts", "#vidaanimal", "#natureza", "#predadores", "#curiosidades"]
                    if is_pt_lang else
                    ["#shorts", "#wildlife", "#nature", "#animals", "#predators"]
                )
            elif niche_norm == "animal_rescue" or any(k in comb_all for k in ("rescue", "puppy", "kitten", "stray", "shelter")):
                default_tags = ["#shorts", "#animalrescue", "#dogrescue", "#wholesome", "#puppies"]
            elif is_vn:
                default_tags = ["#shorts", "#tamlyhoc", "#kynangsong", "#phattrienbanthan"]
            else:
                default_tags = ["#shorts", "#psychology", "#darkpsychology", "#selfmastery"]

            raw_tags_filtered = [
                f"#{str(t).lstrip('#').replace(' ', '')}"
                for t in (raw_data.get("tags") or ["shorts"])
                if str(t).strip()
                and (is_psych_niche or str(t).lstrip('#').lower() not in ("psychology", "darkpsychology", "selfmastery"))
            ]
            tags_line = " ".join(dict.fromkeys(raw_tags_filtered + default_tags))
            summary_body = raw_desc or f"{hook_str} {act2_str}".strip()
            raw_data["description"] = f"{summary_body}\n\n{tags_line}".strip()

        # Đảm bảo câu hỏi tương tác (Engagement Poll / Pinned Question) luôn nằm ngay DÒNG 1 của Description
        # để hiển thị 100% ngay cả khi video đang ở chế độ Hẹn giờ (SCHEDULED) hoặc YouTube API không cho ghim comment!
        pin_q = str(raw_data.get("pinned_comment") or "").strip()
        cur_desc = str(raw_data.get("description") or "").strip()
        if pin_q and pin_q[:30].lower() not in cur_desc.lower():
            lead_q = pin_q if pin_q.startswith("💬") else f"💬 {pin_q}"
            raw_data["description"] = f"{lead_q}\n\n{cur_desc}".strip() if cur_desc else lead_q

        return ScriptContract(**raw_data)
