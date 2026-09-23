import json
import logging
from typing import Dict, Any, Optional
import google.generativeai as genai
from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

class ContentAgent:
    """Agent chịu trách nhiệm lên ý tưởng, viết kịch bản giật gân (hook), phân cảnh và metadata SEO.
    Đã tích hợp KeyRotator để xoay vòng API Keys qua nhiều tài khoản Google Ultra.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self._configure_current_key()

    def _configure_current_key(self):
        current_key = self.rotator.get_key()
        if current_key:
            genai.configure(api_key=current_key)
            self.model = genai.GenerativeModel("models/gemini-3.6-flash")
        else:
            self.model = None
            logger.warning("Chưa có GEMINI_API_KEY nào được cấu hình trong pool!")

    def generate_script(self, topic: str, channel_niche: str = "general", language: str = "en", max_retries: int = 3) -> ScriptContract:
        """
        Sinh kịch bản video Shorts (30-50 giây) giật gân, nhịp điệu nhanh, cuốn hút triệu view.
        Tự động xoay sang key Google Ultra tiếp theo nếu gặp lỗi Rate Limit hoặc Quota.
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
        for attempt in range(max_retries):
            if not self.model:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                raw = json.loads(response.text)
                return ScriptContract(**raw)  # Validate qua Pydantic
            except Exception as e:
                logger.warning(f"Lỗi khi gọi Gemini API (lần {attempt + 1}): {e}")
                # Xoay sang key tiếp theo trong pool của tài khoản Google Ultra khác
                new_key = self.rotator.rotate_key()
                if new_key:
                    self._configure_current_key()
                else:
                    break

        raise RuntimeError("Đã thử tất cả các API Key trong pool nhưng đều thất bại.")

    def refine_script(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en",
        max_retries: int = 3
    ) -> ScriptContract:
        """
        Tiếp thu góp ý của người dùng/nhóm để tinh chỉnh kịch bản hiện tại chuẩn viral.
        Tự động xoay sang key tiếp theo nếu gặp rate limit/quota.
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
        for attempt in range(max_retries):
            if not self.model:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                raw = json.loads(response.text)
                return ScriptContract(**raw)
            except Exception as e:
                logger.warning(f"Lỗi khi refine script với Gemini API (lần {attempt + 1}): {e}")
                new_key = self.rotator.rotate_key()
                if new_key:
                    self._configure_current_key()
                else:
                    break

        raise RuntimeError("Đã thử tất cả các API Key trong pool nhưng không thể cập nhật kịch bản.")

    def chat_brainstorm(self, session_id: str, user_message: str, channel_niche: str = "general", language: str = "vi", max_retries: int = 3) -> str:
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
        # Xây dựng prompt chứa lịch sử hội thoại gần nhất (tối đa 10 lượt)
        recent_history = history[-10:]
        formatted_dialogue = []
        for turn in recent_history:
            role_label = "Creator" if turn["role"] == "user" else "Director"
            formatted_dialogue.append(f"{role_label}: {turn['content']}")
        formatted_dialogue.append(f"Creator: {user_message}")

        full_prompt = f"{system_instruction}\n\nLỊCH SỬ THẢO LUẬN:\n" + "\n".join(formatted_dialogue) + "\n\nDirector:"

        for attempt in range(max_retries):
            if not self.model:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            try:
                response = self.model.generate_content(full_prompt)
                reply_text = response.text.strip()

                # Lưu vào lịch sử phiên làm việc
                history.append({"role": "user", "content": user_message})
                history.append({"role": "model", "content": reply_text})
                # Giới hạn bộ nhớ tối đa 20 lượt
                if len(history) > 20:
                    self.conversations[session_id] = history[-20:]

                return reply_text
            except Exception as e:
                logger.warning(f"Lỗi khi chat Gemini (lần {attempt + 1}): {e}")
                new_key = self.rotator.rotate_key()
                if new_key:
                    self._configure_current_key()
                else:
                    break

        return "⚠️ Hiện tại tất cả Gemini API Key trong pool đều đang bận hoặc quá hạn mức. Vui lòng thử lại sau vài giây!"

    def convert_discussion_to_script(self, session_id: str, channel_niche: str = "general", language: str = "en", max_retries: int = 3) -> ScriptContract:
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
        for attempt in range(max_retries):
            if not self.model:
                raise ValueError("Không có API Key nào hoạt động trong pool!")

            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                raw = json.loads(response.text)
                return ScriptContract(**raw)
            except Exception as e:
                logger.warning(f"Lỗi khi convert script từ discussion (lần {attempt + 1}): {e}")
                new_key = self.rotator.rotate_key()
                if new_key:
                    self._configure_current_key()
                else:
                    break

        raise RuntimeError("Không thể tổng hợp kịch bản từ đoạn chat thảo luận qua Gemini API.")
