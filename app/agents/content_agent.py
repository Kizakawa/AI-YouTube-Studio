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
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemma-4-26b-a4b-it",
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

    def _call_api(self, prompt: str, json_mode: bool = False, timeout: int = 15, max_total_seconds: int = 60) -> str:
        """
        Gọi trực tiếp Gemini REST API với timeout nghiêm ngặt.
        Tự động xoay key và model ngay lập tức nếu gặp 429 (quota), 503 (quá tải) hoặc timeout.
        [FIX #2] Thêm tổng timeout max_total_seconds (mặc định 60s) để tránh kẹt quá lâu.
        """
        import time
        start_time = time.monotonic()
        total_keys = max(len(self.rotator.keys), 1)
        attempts = 0
        max_attempts = total_keys + 1  # Giảm từ total_keys * 2

        for attempt in range(max_attempts):
            # [FIX #2] Kiểm tra tổng thời gian đã chạy
            elapsed = time.monotonic() - start_time
            if elapsed >= max_total_seconds:
                raise RuntimeError(
                    f"Gemini API: Vượt tổng timeout {max_total_seconds}s "
                    f"(đã thử {attempts} requests trong {elapsed:.1f}s)"
                )

            key = self.rotator.get_key()
            if not key:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            for model in AVAILABLE_MODELS:
                # Kiểm tra timeout tổng trước mỗi request
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
                                return parts[0]["text"].strip()
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
                    continue

            # Xoay sang key tiếp theo trong pool
            new_key = self.rotator.rotate_key()
            if not new_key:
                break

        raise RuntimeError(
            f"Đã thử tất cả {attempts} requests qua {total_keys} keys và "
            f"{len(AVAILABLE_MODELS)} models trong {time.monotonic() - start_time:.1f}s — không nhận được phản hồi."
        )

    def _parse_json_script(self, raw_text: str) -> ScriptContract:
        """Helper làm sạch markdown và parse JSON thành ScriptContract"""
        text = raw_text.strip()
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
        prompt = f"""
You are a world-class YouTube Shorts director and scriptwriter producing multi-million view videos for the niche: '{channel_niche}'.
Create a breathtaking, high-retention 40-50 second viral script on the topic: '{topic}'.
Language: {language}

STRICT RETENTION & STORYTELLING FORMULA:
1. 💥 THE 3-SECOND PATTERN INTERRUPT HOOK:
   - Start immediately with an irresistible curiosity gap, shocking counter-intuitive fact, or high-stakes dilemma.
   - Absolutely NO greetings, NO "Did you know?", NO boring intros. Start with immediate impact.
2. ⚡ VISCERAL, HIGH-SPEED PACING:
   - Punchy, concise sentences (6 to 12 words per sentence).
   - High emotional resonance (mystery, awe, suspense, fascination). Zero fluff or filler words.
3. 🌀 ESCALATION & MINDBLOWING CLIMAX:
   - Each scene must build tension or reveal a surprising layer of truth.
   - Deliver a powerful climax or unexpected twist at the 30-40 second mark.
4. 🔁 SEAMLESS REWATCH LOOP:
   - Craft the ending to either loop seamlessly back into the opening sentence or deliver a punchy takeaway.
5. 🎬 CINEMATIC VISUAL KEYWORDS (CRITICAL):
   - For EACH scene, provide a highly specific, evocative English search keyword tailored for cinematic stock footage & AI image rendering.
   - Examples of GREAT visual keywords:
     - "bioluminescent glowing abyssal creature deep dark ocean 4k"
     - "ancient forbidden tomb golden relic volumetric dust rays"
     - "futuristic quantum supercomputer glowing blue holographic core"
   - NEVER use lazy generic words like "ocean", "computer", "city".
6. 🎯 REAL-WORLD ENTITIES & HISTORICAL ACCURACY (VITAL):
   - If the topic or script involves REAL-WORLD people, leaders, celebrities, or historical figures/places (e.g. Vladimir Putin, Donald Trump, Elon Musk, Nikola Tesla, Julius Caesar, Kremlin, White House, NASA):
   - For scenes discussing that person, you MUST explicitly include their exact name/title in visual_keyword (e.g. "Vladimir Putin official portrait", "Vladimir Putin Kremlin address", "Vladimir Putin intense cold stare").
   - NEVER use generic descriptions like "powerful man in boardroom" or "serious politician" when talking about a specific real person! Specifying their real name allows the system to fetch authentic documentary photographs from Web archives.

Return ONLY valid JSON matching this exact schema:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The complete spoken text without any stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "First spoken scene sentence",
      "visual_keyword": "cinematic descriptive English keyword for stock/AI e.g. dark stormy ocean abyss lightning dramatic",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["shorts", "viral", "niche_tags"],
  "description": "Engaging description with hashtags"
}}
"""
        raw_text = self._call_api(prompt, json_mode=True, timeout=30)
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
        raw_text = self._call_api(prompt, json_mode=True, timeout=30)
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
