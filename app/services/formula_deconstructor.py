"""
FormulaDeconstructor — Dịch vụ giải phẫu và nhân bản công thức kịch bản video viral.

Lấy cảm hứng từ hệ thống OpenMontage & các kênh Shorts triệu view:
1. Giải mã cấu trúc nhịp điệu (Retention Curve Deconstruction):
   - Phân tích Hook (0-3s), nhịp ngắt thị giác (Pattern Interrupts), tốc độ nói (WPM).
   - Tách các nhịp tâm lý (Beats): Hook -> Agitation -> Complication -> Climax -> Infinite Loop.
2. Nhân bản kịch bản nguyên bản (Blueprint-Guided Generation):
   - Ứng dụng khung sườn nhịp điệu đã kiểm chứng để sinh kịch bản hoàn toàn mới,
     không trùng lặp bản quyền, giữ chân người xem đạt retention > 85%.
"""

import re
import json
import logging
import time
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.agents.content_agent import ContentAgent, build_language_integrity_instruction
from app.schemas.contracts import ScriptContract, SceneContract
from app.services.video_repurposer import VideoRepurposerService

logger = logging.getLogger(__name__)


class FormulaDeconstructor:
    """Giải phẫu cấu trúc kịch bản video viral & sinh kịch bản mới theo khuôn mẫu nhịp điệu."""

    def __init__(self):
        self.content_agent = ContentAgent()
        self.repurposer = VideoRepurposerService()

    def deconstruct_from_source(
        self,
        url_or_text: str,
        target_lang: str = "en"
    ) -> Dict[str, Any]:
        """
        Nhận diện URL (YouTube / video file) hoặc đoạn transcript văn bản thô,
        trích xuất phụ đề và giải phẫu thành Blueprint nhịp điệu viral.
        """
        transcript_text = ""
        metadata: Dict[str, Any] = {
            "source": "raw_text",
            "title": "Video Phân Tích",
            "duration_seconds": 60.0,
            "author": "Creator",
            "thumbnail": ""
        }

        # Kiểm tra xem đầu vào có phải là link hay văn bản kịch bản trực tiếp
        is_link = bool(re.search(r'https?://', url_or_text) or len(url_or_text.strip()) == 11)
        if is_link:
            try:
                meta = self.repurposer.get_video_metadata(url_or_text)
                metadata.update(meta)
                vid = meta.get("video_id")
                if vid:
                    raw_subs = self.repurposer.fetch_transcript(vid, target_lang=target_lang)
                    if raw_subs:
                        # Gom nhóm các dòng transcript kèm mốc giây
                        lines = []
                        for s in raw_subs:
                            start_s = s.get("start", 0)
                            txt = s.get("text", "").strip()
                            if txt:
                                lines.append(f"[{int(start_s // 60):02d}:{int(start_s % 60):02d}] {txt}")
                        transcript_text = "\n".join(lines)
            except Exception as e:
                logger.warning(f"[FormulaDeconstructor] Không thể lấy transcript tự động từ link: {e}")

        if not transcript_text:
            transcript_text = url_or_text.strip()
            if not transcript_text:
                raise ValueError("Không tìm thấy nội dung transcript hoặc lời thoại để giải phẫu.")

        # Gửi transcript cho Gemini giải phẫu công thức nhịp điệu
        blueprint = self._ai_extract_blueprint(transcript_text, metadata, target_lang=target_lang)
        return {
            "metadata": metadata,
            "transcript_sample": transcript_text[:500] + ("..." if len(transcript_text) > 500 else ""),
            "blueprint": blueprint
        }

    def _ai_extract_blueprint(
        self,
        transcript_text: str,
        metadata: Dict[str, Any],
        target_lang: str = "en"
    ) -> Dict[str, Any]:
        """Dùng Gemini phân tích cấu trúc nhịp điệu và trả về Blueprint chuẩn."""
        prompt = f"""You are a World-Class Viral Video Director and Retention Engineer specializing in YouTube Shorts and TikTok.
Analyze the following transcript of a viral video and DECONSTRUCT its exact storytelling architecture, rhythm, pacing, and emotional curve.

[VIDEO METADATA]
Title: {metadata.get('title', 'Unknown')}
Estimated Duration: {metadata.get('duration_seconds', 60)}s

[FULL TRANSCRIPT]
\"\"\"{transcript_text[:4000]}\"\"\"

CRITICAL GOAL:
Reverse-engineer the psychological and structural framework so we can replicate this exact retention curve on ANY new topic.

Return ONLY a valid JSON object matching this schema:
{{
  "title": "{metadata.get('title', 'Analyzed Short')}",
  "total_estimated_seconds": float,
  "words_per_minute": int,
  "hook_analysis": {{
    "text": "The exact opening line (0-3s)",
    "duration_seconds": float,
    "hook_archetype": "Bold Assertion | Curiosity Gap | Negative Warning | Visual Shock | Question | Paradox",
    "retention_trigger": "Why viewers physically stop scrolling on this sentence",
    "visual_cue": "Recommended opening visual motion"
  }},
  "retention_curve": {{
    "pacing_style": "Fast & Punchy (every 2-3s) | Escalating Mystery (every 4-5s) | Dramatic Story Arc",
    "pattern_interrupt_interval_seconds": float,
    "tension_peak_second": float,
    "emotional_arc": "e.g., Confusion -> Shock -> Realization -> Amazement"
  }},
  "beats": [
    {{
      "beat_id": 1,
      "time_range": "0:00 - 0:03",
      "duration_seconds": 3.0,
      "role": "HOOK",
      "story_function": "Stop the scroll with an impossible claim",
      "visual_motion_type": "hyper_zoom"
    }},
    {{
      "beat_id": 2,
      "time_range": "0:03 - 0:12",
      "duration_seconds": 9.0,
      "role": "AGITATION",
      "story_function": "Deepen the stakes, why this matters",
      "visual_motion_type": "pan_left"
    }},
    {{
      "beat_id": 3,
      "time_range": "0:12 - 0:28",
      "duration_seconds": 16.0,
      "role": "CORE_EVIDENCE",
      "story_function": "Escalating proof with rapid visual changes",
      "visual_motion_type": "cinematic_drift"
    }},
    {{
      "beat_id": 4,
      "time_range": "0:28 - 0:45",
      "duration_seconds": 17.0,
      "role": "CLIMAX_TWIST",
      "story_function": "The unexpected revelation",
      "visual_motion_type": "zoom_in"
    }},
    {{
      "beat_id": 5,
      "time_range": "0:45 - 0:55",
      "duration_seconds": 10.0,
      "role": "SEAMLESS_LOOP",
      "story_function": "Sentence cleanly links back to Beat 1 hook for infinite loops",
      "visual_motion_type": "warp_zoom"
    }}
  ],
  "loop_mechanism": {{
    "closing_phrase": "The final phrase of the video",
    "connects_to_hook": "Explanation of how closing phrase connects back to Beat 1"
  }},
  "viral_formula_summary": "1-sentence actionable summary of this retention formula"
}}"""

        raw_response = self.content_agent._call_api(prompt, json_mode=True, max_total_seconds=40)
        try:
            cleaned = re.sub(r'^```json\s*', '', raw_response.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            return json.loads(cleaned)
        except Exception as e:
            logger.error(f"[FormulaDeconstructor] Lỗi parse JSON blueprint: {e}\nRaw:\n{raw_response}")
            # Fallback blueprint mặc định nếu model trả về lỗi format
            return {
                "title": metadata.get("title", "Standard Viral Blueprint"),
                "total_estimated_seconds": 50.0,
                "words_per_minute": 150,
                "hook_analysis": {
                    "text": "You will never look at this the same way again.",
                    "duration_seconds": 3.0,
                    "hook_archetype": "Curiosity Gap",
                    "retention_trigger": "High curiosity trigger",
                    "visual_cue": "Fast zoom on primary subject"
                },
                "retention_curve": {
                    "pacing_style": "Fast & Punchy",
                    "pattern_interrupt_interval_seconds": 3.5,
                    "tension_peak_second": 35.0,
                    "emotional_arc": "Curiosity -> Tension -> Payoff"
                },
                "beats": [
                    {"beat_id": 1, "time_range": "0:00 - 0:03", "duration_seconds": 3.0, "role": "HOOK", "story_function": "Scroll stopper", "visual_motion_type": "zoom_in"},
                    {"beat_id": 2, "time_range": "0:03 - 0:15", "duration_seconds": 12.0, "role": "TENSION", "story_function": "Build curiosity", "visual_motion_type": "pan_left"},
                    {"beat_id": 3, "time_range": "0:15 - 0:35", "duration_seconds": 20.0, "role": "CLIMAX", "story_function": "Major reveal", "visual_motion_type": "cinematic_drift"},
                    {"beat_id": 4, "time_range": "0:35 - 0:50", "duration_seconds": 15.0, "role": "LOOP", "story_function": "Seamless loop", "visual_motion_type": "zoom_out"}
                ],
                "loop_mechanism": {
                    "closing_phrase": "And that is the exact reason why...",
                    "connects_to_hook": "Loops back to the beginning statement"
                },
                "viral_formula_summary": "High-velocity Hook-Tension-Payoff with a seamless loop connector."
            }

    def generate_script_from_blueprint(
        self,
        blueprint: Dict[str, Any],
        new_topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: str = ""
    ) -> ScriptContract:
        """
        Nhân bản kịch bản mới 100% nguyên bản trên chủ đề mới,
        áp dụng nghiêm ngặt cấu trúc nhịp điệu (beats, timing, hook, loop) từ Blueprint.
        """
        beats = blueprint.get("beats", [])
        hook_info = blueprint.get("hook_analysis", {})
        loop_info = blueprint.get("loop_mechanism", {})
        wpm = blueprint.get("words_per_minute", 150)
        target_dur = blueprint.get("total_estimated_seconds", 50.0)

        beats_instructions = []
        for b in beats:
            b_id = b.get("beat_id", 1)
            b_role = b.get("role", "SCENE")
            b_dur = b.get("duration_seconds", 4.0)
            b_func = b.get("story_function", "")
            b_mot = b.get("visual_motion_type", "zoom_in")
            beats_instructions.append(
                f"- Scene {b_id} (Role: {b_role}, Target: ~{b_dur}s, Motion: {b_mot}): {b_func}"
            )

        lang_instruction = build_language_integrity_instruction(language)

        prompt = f"""You are an Elite Viral YouTube Shorts Scriptwriter.
Task: Write a brand new, highly engaging, 100% original script for the following topic:
Topic: "{new_topic}"
Niche: {channel_niche}
Director Notes: {director_notes or 'Focus on maximum viewer retention and high emotional engagement.'}

{lang_instruction}

═══════════════════════════════════════════════════════════════════
🧬 VIRAL RETENTION BLUEPRINT CLONE CONSTRAINTS (STRICTLY REQUIRED)
═══════════════════════════════════════════════════════════════════
You MUST strictly follow this proven retention architecture extracted from a viral video:
1. TARGET DURATION & PACING: Total ~{target_dur:.0f} seconds. Fast-paced delivery (~{wpm} WPM).
2. OPENING HOOK (0-3 SECONDS):
   - Hook Archetype: {hook_info.get('hook_archetype', 'Curiosity Gap')}
   - Hook Trigger Goal: {hook_info.get('retention_trigger', 'Stop scrolling')}
   - Scene 1 narration MUST be concise, visceral, and punchy.
3. SCENE-BY-SCENE BEAT STRUCTURE:
{chr(10).join(beats_instructions)}
4. INFINITE LOOP ENDING:
   - The very last sentence of the final scene MUST be crafted as a seamless grammatical/thematic bridge back to the opening hook (Scene 1).
   - Mechanism goal: {loop_info.get('connects_to_hook', 'Seamless loop')}
5. VISUAL KEYWORDS & PROMPTS:
   - 'stock_query': 2-3 precise English keywords for Pexels 9:16 portrait stock video footage (e.g. 'leopard hunting savana', 'deep ocean glowing creature', 'storm clouds lightning').
   - 'visual_prompt': Highly detailed 8K cinematic visual prompt.

Return ONLY a valid JSON object matching the ScriptContract schema:
{{
  "title": "High CTR Click-Worthy Title (under 60 chars)",
  "hook": "Opening hook sentence (matches Scene 1 narration)",
  "full_narration": "All scenes narration joined with spaces",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Opening line",
      "visual_keyword": "2-3 English search words",
      "stock_query": "specific video query",
      "visual_prompt": "Cinematic visual description for AI generation, 8k",
      "estimated_seconds": {beats[0].get('duration_seconds', 3.0) if beats else 3.0},
      "camera_motion": "zoom_in",
      "media_type": "video"
    }}
  ],
  "tags": ["shorts", "viral", "{channel_niche}"],
  "description": "Engaging description with #shorts #viral",
  "pinned_comment": "Curiosity provoking pinned comment to drive discussion",
  "viral_formula": "Cloned Formula: {blueprint.get('viral_formula_summary', 'Blueprint Retention Curve')}",
  "thumbnail_text": "2-3 HIGH IMPACT WORDS"
}}"""

        raw_response = self.content_agent._call_api(prompt, json_mode=True, max_total_seconds=60)
        try:
            cleaned = re.sub(r'^```json\s*', '', raw_response.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            data = json.loads(cleaned)
            for idx, sc in enumerate(data.get("scenes", [])):
                sc["scene_id"] = idx + 1
                if not sc.get("visual_keyword"):
                    sc["visual_keyword"] = sc.get("stock_query") or sc.get("visual_prompt") or data.get("title", "cinematic")
                if "estimated_seconds" not in sc or not sc.get("estimated_seconds"):
                    sc["estimated_seconds"] = 3.5

            if isinstance(data.get("tags"), str):
                data["tags"] = [t.strip().lstrip("#") for t in data["tags"].split(",") if t.strip()]

            script_contract = ScriptContract.model_validate(data)
            return script_contract
        except Exception as e:
            logger.error(f"[FormulaDeconstructor] Lỗi validate ScriptContract từ blueprint: {e}\nRaw:\n{raw_response}")
            return self.content_agent.generate_script(
                topic=f"{new_topic} (Structure: {blueprint.get('viral_formula_summary', 'Viral Retention')})",
                channel_niche=channel_niche,
                language=language,
                director_notes=f"Follow beats: {beats_instructions[:3]}"
            )
