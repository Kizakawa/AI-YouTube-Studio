import json
import logging
import requests
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]

class ContentAgent:
    """Agent chịu trách nhiệm lên ý tưởng, viết kịch bản giật gân (hook), phân cảnh và metadata SEO.
    Đã tích hợp KeyRotator để xoay vòng API Keys qua nhiều tài khoản Google Ultra bằng direct HTTP REST
    nhằm đảm bảo tốc độ cao, không chặn event loop và không bị kẹt retry gRPC.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self.conversations: Dict[str, List[Dict[str, str]]] = {}

    def _call_api(self, prompt: str, json_mode: bool = False, timeout: int = 25, max_total_seconds: int = 150) -> str:
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
                    "contents": [{"parts": [{"text": prompt}]}]
                }
                if json_mode:
                    payload["generationConfig"] = {"responseMimeType": "application/json"}

                try:
                    attempts += 1
                    r = requests.post(url, json=payload, timeout=timeout)
                    if r.status_code == 200:
                        data = r.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts and "text" in parts[0]:
                                logger.info(f"✅ Gemini API ({model}) thành công với key {key[:8]}... trong {time.monotonic()-start_time:.1f}s")
                                return parts[0]["text"].strip()
                    elif r.status_code in (429, 403):
                        logger.warning(f"⚠️ Key {key[:8]}... gặp lỗi {r.status_code} (hết quota). Chuyển sang key tiếp theo ngay lập tức...")
                        self.rotator.rotate_key()
                        key_failed_quota = True
                        break  # Thoát ngay vòng lặp model của key này
                    elif r.status_code == 503 and json_mode:
                        # Fallback: Thử lại không dùng responseMimeType (tránh 503 trên engine structured JSON)
                        logger.info(f"Gemini API ({model}) gặp 503 với structured output, thử lại chế độ raw JSON...")
                        time.sleep(1.0)
                        try:
                            fallback_payload = {"contents": [{"parts": [{"text": prompt}]}]}
                            r_fb = requests.post(url, json=fallback_payload, timeout=timeout)
                            if r_fb.status_code == 200:
                                data = r_fb.json()
                                candidates = data.get("candidates", [])
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts", [])
                                    if parts and "text" in parts[0]:
                                        logger.info(f"✅ Gemini API ({model}) thành công qua fallback raw JSON trong {time.monotonic()-start_time:.1f}s")
                                        return parts[0]["text"].strip()
                        except Exception as e_fb:
                            logger.debug(f"Fallback raw JSON lỗi: {e_fb}")
                        time.sleep(1.0)
                        continue
                    else:
                        err_msg = ""
                        try:
                            err_msg = r.json().get("error", {}).get("message", "")[:80]
                        except Exception:
                            err_msg = r.text[:80]
                        logger.warning(f"Gemini API ({model}) trả lỗi {r.status_code} với key {key[:8]}...: {err_msg}")
                        if r.status_code == 503:
                            time.sleep(1.0)
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
        """Helper làm sạch markdown và parse JSON thành ScriptContract"""
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
        return ScriptContract(**raw_data)

    def generate_script(self, topic: str, channel_niche: str = "general", language: str = "en") -> ScriptContract:
        """
        Sinh kịch bản video Shorts (30-50 giây) giật gân, nhịp điệu nhanh, cuốn hút triệu view.
        """
        prompt = f"""You are a world-class YouTube Shorts director creating high-retention viral videos in '{channel_niche}'.
Topic: '{topic}'
Language: {language}

STRICT RETENTION & STORYTELLING FORMULA:
1. 3-Second Pattern Interrupt Hook: Shocking statement, dilemma, or curiosity gap. No boring intros.
2. High-Speed Visceral Pacing: 6-12 words per sentence, high emotion, zero fluff.
3. Escalation & Climax: Build tension each scene, climax at 30-40 seconds, ending loops seamlessly.
4. Cinematic Visual Keywords & Real-World Accuracy:
   - For real historical figures or places: EVERY scene's visual_keyword MUST name the figure and authentic historical era/action (e.g. '1911 historic ship port', 'archival portrait'). Do NOT use generic terms.
   - For abstract topics: Highly specific evocative 4K cinematic English keywords.
   - Identify the main real-world entity in 'primary_entity' (or null if abstract).

Return ONLY a valid JSON object matching this schema:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "primary_entity": "Full official name of main person/figure or null",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "Complete spoken text without stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Spoken sentence for this scene",
      "visual_keyword": "cinematic descriptive English keyword with person name and era context",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["shorts", "viral"],
  "description": "Engaging description with hashtags"
}}
Do not write any text or markdown outside the JSON block.
"""
        raw_text = self._call_api(prompt, json_mode=False, timeout=30)
        return self._parse_json_script(raw_text)

    def refine_script(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en"
    ) -> ScriptContract:
        """
        Tiếp thu góp ý của người dùng/nhóm để tinh chỉnh kịch bản hiện tại chuẩn viral.
        """
        prompt = f"""
You are an elite viral YouTube Shorts director.
We have an existing script, but the creator and team provided specific feedback/revisions:

USER FEEDBACK:
"{feedback}"

CURRENT SCRIPT:
{current_script.model_dump_json(indent=2)}

Please refine and rewrite the script strictly adhering to the user's feedback while maintaining top-tier viewer retention, fast pacing, and cinematic visual keywords.
Language: {language}

Strict requirements:
1. Revise the narration, hook, or visual keywords according to the feedback.
2. Fast-paced, punchy, concise sentences (35-50 seconds total).
3. Highly descriptive, cinematic English visual keywords for each scene.
4. Return ONLY valid JSON matching this schema:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without any stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Text spoken in this scene",
      "visual_keyword": "cinematic descriptive English keyword for stock/AI",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags"
}}
"""
        raw_text = self._call_api(prompt, json_mode=False, timeout=30)
        return self._parse_json_script(raw_text)

    def chat_brainstorm(
        self,
        session_id: str,
        user_message: str,
        channel_niche: str = "general",
        language: str = "vi"
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
            return "⚠️ Hiện tại các Gemini API Key trong pool đều đang bận hoặc quá hạn mức. Vui lòng thử lại sau vài giây!"

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

        prompt = f"""
You are an expert YouTube Shorts producer.
Based on the following brainstorm discussion between a creator and director:
---
{dialogue_text}
---

Synthesize this entire discussion into a complete, high-retention 40-50 second YouTube Shorts script.
Niche: '{channel_niche}'
Script Language (narration & title): {language}

Strict requirements:
1. Choose the absolute BEST hook from the discussion for the first 3 seconds.
2. Fast-paced, punchy, concise sentences.
3. Provide visual keywords for each scene to search on stock footage sites (Pexels).
4. Return ONLY valid JSON matching this schema:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Spoken text in this scene",
      "visual_keyword": "search keyword for stock video e.g. dark aesthetic city street",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags"
}}
"""
        raw_text = self._call_api(prompt, json_mode=True, timeout=30)
        return self._parse_json_script(raw_text)
