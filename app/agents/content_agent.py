import json
import logging
from typing import Dict, Any, Optional
import google.generativeai as genai
from config.settings import settings

logger = logging.getLogger(__name__)

class ContentAgent:
    """Agent chịu trách nhiệm lên ý tưởng, viết kịch bản giật gân (hook), phân cảnh và metadata SEO."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel("gemini-1.5-flash")
        else:
            self.model = None
            logger.warning("GEMINI_API_KEY chưa được cấu hình!")

    def generate_script(self, topic: str, channel_niche: str = "general", language: str = "en") -> Dict[str, Any]:
        """
        Sinh kịch bản video Shorts (30-50 giây) có cấu trúc:
        - title: Tiêu đề giật tít CTR cao
        - hook: Câu mở đầu giữ chân 3s
        - full_narration: Toàn bộ văn bản để đọc TTS
        - scenes: Danh sách cảnh [ { text, visual_keyword, duration_seconds } ]
        - tags: Bộ thẻ tags
        - description: Mô tả video chuẩn SEO
        """
        if not self.model:
            raise ValueError("Chưa thiết lập GEMINI_API_KEY trong .env")

        prompt = f"""
You are an elite YouTube Shorts scriptwriter specializing in viral content for the niche: '{channel_niche}'.
Write a highly engaging 40-50 second script on the topic: '{topic}'.
Language: {language}

Strict requirements:
1. The first 3 seconds must have an irresistible HOOK.
2. Fast-paced, punchy, concise sentences.
3. Provide visual keywords for each scene to search on stock footage sites (Pexels).
4. Return ONLY valid JSON matching this schema:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without any stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Text spoken in this scene",
      "visual_keyword": "search keyword for stock video e.g. dark aesthetic city street",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags"
}}
"""
        response = self.model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
