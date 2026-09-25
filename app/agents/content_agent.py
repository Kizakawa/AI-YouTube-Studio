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

        return ScriptContract(**raw_data)

    def generate_script(self, topic: str, channel_niche: str = "general", language: str = "en") -> ScriptContract:
        """
        Sinh kịch bản video Shorts viral tối ưu thuật toán giữ chân (AVD > 75%, thời lượng vàng 20-28 giây).
        Nhịp cảnh siêu nhanh (2.5-3.5s/cảnh), Hook 3s đầu đảo ngược định kiến, vòng lặp vô tận (Infinite Shorts Loop).
        """
        prompt = f"""You are an elite YouTube Shorts director and viral retention engineer for a {channel_niche} channel.
Topic: '{topic}'
Script Language: {language}

═══════════════════════════════════════════════════════════
VIRAL RETENTION ALGORITHM — CORE RULES (AVD > 80%)
═══════════════════════════════════════════════════════════
1. ULTRA-FOCUSED DURATION (20 - 28 SECONDS TOTAL):
   - Total spoken word count: strictly 50 to 75 words.
   - Exact scene count: strictly 6 to 8 scenes.
   - Pacing: 2.5 to 3.5 seconds per scene (6 to 10 words per scene).
   - NEVER drag a scene past 4 seconds. Fast visual cuts prevent viewer swiping.

2. 3-SECOND PATTERN INTERRUPT HOOK (SCENE 1):
   - First 3 seconds MUST deliver an extreme curiosity gap, cognitive dissonance, or unbelievable fact.
   - ABSOLUTELY FORBIDDEN: greetings ("Hello guys"), meta-introductions ("In this video", "Today we discover"), cliches ("Have you ever wondered", "Did you know that").
   - Start immediately IN MEDIAS RES with high tension.

3. THE INFINITE SHORTS LOOP (SEAMLESS REPLAY MULTIPLIER):
   - The final scene (Scene 6/7/8) MUST NOT have a standard outro or goodbye ("Thanks for watching", "Subscribe", "Comment below").
   - Instead, the final sentence MUST end on an open cliffhanger, preposition, or setup clause that syntactically flows directly back into the opening Hook sentence of Scene 1.
   - When the YouTube Shorts player loops the video, the viewer should not realize it ended.

4. CONCRETE PHYSICAL STOCK QUERIES (NO ABSTRACT WORDS):
   - stock_query is 2-3 English search words strictly for Pexels stock video/photo search.
   - HARD RULE: stock_query MUST be a CONCRETE, VISIBLE, PHYSICAL OBJECT, PERSON, OR ACTION.
   - STRICTLY FORBIDDEN abstract words: "psychology", "minimalism", "authority", "power", "wealth", "mindset", "discipline", "history", "secret", "truth".
   - Examples:
     * BAD: "psychology authority" → GOOD: "man wearing suit"
     * BAD: "extreme minimalism" → GOOD: "worn leather sandals" or "empty wooden room"
     * BAD: "greed power" → GOOD: "gold coins falling" or "hands holding cash"
     * BAD: "ancient sacrifice" → GOOD: "mayan pyramid jungle" or "smoke rising altar"

5. VISUAL PROMPT (FOR CINEMATIC AI IMAGE GENERATION):
   - Concrete cinematic format: "[Main subject + specific action], [camera angle & movement], [cinematic lighting style], [environment detail]"
   - No literal metaphors: always illustrate the actual historical/scientific phenomenon.
   - Every scene must be visually unique (no repeating subjects or camera angles).

6. TITLE & LANGUAGE INTEGRITY:
   - Title MUST be in {language} and strictly focused on click-through rate (CTR > 10%).
   - DO NOT include channel tags, country names, or suffixes like "- VN", "- VI", "(Vietnam)".
   - Clean, punchy title under 60 characters with emojis.

═══════════════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no markdown, no extra text:
═══════════════════════════════════════════════════════════
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "primary_entity": "Full official name of main person/figure or null",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "Complete spoken text without stage directions (50-75 words)",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Spoken sentence for this scene (6-10 words)",
      "visual_keyword": "concrete search keywords",
      "visual_prompt": "Cinematic visual description: subject + camera angle + lighting + background",
      "stock_query": "concrete physical nouns",
      "estimated_seconds": 3.0
    }}
  ],
  "tags": ["shorts", "viral"],
  "description": "Engaging description with hashtags"
}}
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
You are an elite viral YouTube Shorts director and cinematographer.
We have an existing script, but the creator and team provided specific feedback/revisions:

USER FEEDBACK:
"{feedback}"

CURRENT SCRIPT:
{current_script.model_dump_json(indent=2)}

REFINE the script strictly adhering to the user's feedback.
Language: {language}

HARD RETENTION RULES:
1. Fast-paced, punchy, concise sentences: strictly 20-28 seconds total (50-75 words, 6-8 scenes, 2.5-3.5s per scene).
2. NO ABSTRACT stock_query: 2-3 English CONCRETE PHYSICAL NOUNS only for Pexels.
   NO "psychology", "minimalism", "authority", "power". Use physical objects/actions only.
3. THE INFINITE SHORTS LOOP: The final scene must seamlessly connect back into Scene 1's hook.
4. NO LITERAL METAPHORS in visual_prompt: show the actual phenomenon, not metaphorical words.
5. NO DUPLICATE SCENES: every visual_prompt must describe a visually distinct subject and setting.
6. visual_prompt format: "[Subject + action], [camera angle], [lighting], [environment]"
7. Return ONLY valid JSON:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without any stage directions (50-75 words)",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Text spoken in this scene (6-10 words)",
      "visual_keyword": "concrete search keywords",
      "visual_prompt": "Detailed cinematic scene: subject + camera angle + lighting + environment",
      "stock_query": "concrete physical nouns",
      "estimated_seconds": 3.0
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
You are an expert YouTube Shorts producer and cinematographer.
Based on the following brainstorm discussion between a creator and director:
---
{dialogue_text}
---

Synthesize this entire discussion into a complete, high-retention 40-50 second YouTube Shorts script.
Niche: '{channel_niche}'
Script Language (narration & title): {language}

HARD RULES:
1. Choose the absolute BEST hook from the discussion for the first 3 seconds.
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
