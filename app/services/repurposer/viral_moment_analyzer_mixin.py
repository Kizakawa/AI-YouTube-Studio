"""app.services.repurposer.viral_moment_analyzer_mixin — Phân tích khoảnh khắc viral & kịch bản Mashup bằng LLM."""
"""
VideoRepurposerService — Tái chế video dài thành Shorts viral chuẩn bản quyền & tối ưu thuật toán.

Đặc tính chính:
1. An toàn bản quyền (Copyright & Fair Use):
   - Chế độ "Kênh nhà" (Self-Owned Content): Tái chế video dài của chính kênh thành 5-10 Shorts.
   - Chế độ "Bình luận Chuyển hóa" (Transformative Fair-Use Commentary): Bổ sung góc nhìn phân tích,
     trích dẫn nguồn minh bạch (Credit watermark), trích dẫn clip ngắn kèm luận điểm mới.
2. Phát hiện phân đoạn Viral (AI Highlight Detection):
   - Sử dụng Gemini AI phân tích transcript/ngữ cảnh để tìm 3-5 khoảnh khắc đắt giá nhất (30-55s).
   - Chấm điểm Viral Score (1-100), Hook Score, gợi ý tiêu đề Shorts giật tít và lý do viral.
3. Chuyển đổi khung hình 9:16 thông minh & Phụ đề Karaoke Niche Profile:
   - Layout Blur Background hoặc Center Crop chuẩn Shorts/TikTok/Reels.
   - Tự động đồng bộ font chữ, màu kinetic karaoke theo Niche Profile của kênh.
"""

import os
import re
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from config.settings import settings
from app.agents.content_agent import ContentAgent
from app.services.niche_manager import get_niche, get_niche_subtitle_style
from app.utils.subtitle_helper import SubtitleHelper, ASS_WHITE, ASS_YELLOW, ASS_BLACK
from app.utils.ffmpeg_helper import run_ffmpeg, probe_file, get_duration_ms, detect_audio_speech_start, BT709_COLOR_ARGS, build_atempo_filter
from app.services.repurposer_qa import preflight_repurpose_qa

logger = logging.getLogger(__name__)

REPURPOSER_DIR = Path("output/repurposer")
DOWNLOADS_DIR = REPURPOSER_DIR / "downloads"
CLIPS_DIR = REPURPOSER_DIR / "clips"
SUBS_DIR = REPURPOSER_DIR / "subtitles"

for d in [REPURPOSER_DIR, DOWNLOADS_DIR, CLIPS_DIR, SUBS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

import time
import uuid
import threading

# Giới hạn tối đa 2 render FFmpeg đồng thời trên toàn server để tránh đơ CPU/tràn RAM
_render_semaphore = threading.Semaphore(2)

# Lock download theo video_id để tránh việc 2 máy cùng tải đè hoặc xung đột file
_download_locks: Dict[str, threading.Lock] = {}
_dl_pool_lock = threading.Lock()

def _get_video_download_lock(vid: str) -> threading.Lock:
    with _dl_pool_lock:
        if vid not in _download_locks:
            _download_locks[vid] = threading.Lock()
        return _download_locks[vid]


def extract_youtube_video_id(url_or_id: str) -> Optional[str]:
    """Trích xuất Video ID từ các định dạng URL YouTube hoặc Bilibili (BV1.../av.../b23.tv)."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    try:
        from app.services.media.bilibili_fetcher import extract_bilibili_id, is_bilibili_source
        if is_bilibili_source(url_or_id):
            b_parsed = extract_bilibili_id(url_or_id)
            if b_parsed and b_parsed.get("id"):
                return b_parsed["id"]
    except Exception:
        pass
    if len(url_or_id) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([0-9A-Za-z_-]{11})',
        r'[\?&]v=([0-9A-Za-z_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return None



class ViralMomentAnalyzerMixin:
    def _ensure_viral_english_title(self, raw_title: str, niche_id: str, segment_idx: int = 1) -> str:
        """
        Kiểm tra và chuẩn hóa tiêu đề:
        - Đảm bảo 100% TIẾNG ANH chuyên nghiệp, KHÔNG tiếng Việt.
        - KHÔNG giữ nguyên tên video gốc.
        - Đảm bảo có thẻ #Shorts ở cuối.
        """
        cfg = self._NICHE_FALLBACK_CFG.get(niche_id, {})
        fallback_titles = cfg.get("fallback_titles", [
            "You Won't Believe What Happened Next #Shorts",
            "The Shocking Truth Almost Everyone Missed #Shorts",
            "Watch What Happens In The Final 5 Seconds #Shorts"
        ])
        
        # Regex kiểm tra ký tự tiếng Việt
        vietnamese_regex = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]', re.IGNORECASE)
        
        t = (raw_title or "").strip()
        # Nếu rỗng, hoặc có tiếng Việt, hoặc quá ngắn: dùng tiêu đề tiếng Anh mẫu theo ngách
        if not t or len(t) < 10 or vietnamese_regex.search(t):
            idx = (segment_idx - 1) % len(fallback_titles)
            return fallback_titles[idx]
            
        # Loại bỏ các prefix cũ như "🔥 BÍ MẬT GÂY SỐC: " nếu có
        t = re.sub(r'^(?:🔥|⚡|🐾|🧠|🌊|✨)?[^:]+:\s*', '', t).strip()
        
        # Đảm bảo có #Shorts
        if not re.search(r'#shorts\b', t, re.IGNORECASE):
            t = f"{t} #Shorts"
            
        return t

    def _ensure_english_narration(self, text: str, niche_id: str = "general") -> str:
        """
        Đảm bảo văn bản lời thoại/thuyết minh 100% TIẾNG ANH (US English) để xuất khẩu kênh quốc tế.
        Nếu phát hiện văn bản tiếng Việt: Tự động chuyển dịch sang tiếng Anh viral chuẩn YouTube Shorts.
        """
        from app.agents.voice_agent import VoiceAgent
        clean = SubtitleHelper.sanitize_subtitle_text(text or "").strip()
        if not clean:
            cfg = self._NICHE_FALLBACK_CFG.get(niche_id, {})
            return cfg.get("hook_quote", "Watch closely right here — this incredible moment changed everything!")

        if not VoiceAgent._is_vietnamese(clean):
            return clean

        logger.info(f"🇺🇸 [Repurposer] Phát hiện văn bản tiếng Việt: '{clean[:40]}...'. Đang tự động chuyển ngữ sang Tiếng Anh...")
        try:
            from app.agents.content_agent import ContentAgent
            ca = ContentAgent()
            prompt = (
                f"You are a professional viral YouTube Shorts copywriter.\n"
                f"Translate and adapt this Vietnamese narration into conversational, high-retention AMERICAN ENGLISH "
                f"for a YouTube Short in niche '{niche_id}'.\n\n"
                f"Vietnamese text: \"{clean}\"\n\n"
                f"STRICT RULES:\n"
                f"1. Output ONLY the English narration text.\n"
                f"2. Keep it punchy, emotional, and under 3 short sentences.\n"
                f"3. Absolutely NO markdown quotes, no explanations, no Vietnamese characters."
            )
            translated = ca._call_api(prompt, timeout=12)
            if translated and len(translated.strip()) > 5 and not VoiceAgent._is_vietnamese(translated):
                english_res = SubtitleHelper.sanitize_subtitle_text(translated)
                logger.info(f"🇺🇸 [Repurposer] Chuyển ngữ thành công: '{english_res[:50]}...'")
                return english_res
        except Exception as e:
            logger.warning(f"[Repurposer] Lỗi gọi AI dịch tiếng Anh: {e}")

        # Fallback bằng câu chuyện tiếng Anh hấp dẫn theo ngách
        cfg = self._NICHE_FALLBACK_CFG.get(niche_id, {})
        hook = cfg.get("hook_quote", "Watch closely right here — this incredible moment changed everything!")
        summary = cfg.get("summary", "An unexpected turnaround in a dramatic situation.")
        return f"{hook} {summary}"

    def _build_niche_fallback_moments(
        self,
        niche_profile: Dict[str, Any],
        video_title: str,
        video_duration: float,
    ) -> List[Dict[str, Any]]:
        """
        Tạo fallback moments dựa trên DNA của niche thay vì generic giật gân.
        Tiêu đề, hook, summary hoàn toàn bằng Tiếng Anh chuyên nghiệp.
        """
        niche_id = niche_profile.get("id", "general")
        cfg = self._NICHE_FALLBACK_CFG.get(niche_id)

        # Nếu không có config niche, dùng config tổng hợp bằng tiếng Anh
        if not cfg:
            cfg = {
                "moment_types": ["✨ VIRAL MOMENT", "💡 UNBELIEVABLE TRUTH", "🎯 SHOCKING REALITY"],
                "fallback_titles": [
                    "You Won't Believe What Happened Next #Shorts",
                    "The Shocking Truth Almost Everyone Missed #Shorts",
                    "Watch What Happens In The Final 5 Seconds #Shorts"
                ],
                "hook_quote": "Watch closely right here — this changed everything...",
                "hook_options": [
                    {"type": "Curiosity", "hook": "Why did everyone completely overlook this detail until now?"},
                    {"type": "Value",     "hook": "The most important takeaway hidden inside this ordinary scene!"},
                    {"type": "Question",  "hook": "If you were in this situation, how would you react?"}
                ],
                "summary": "High-impact standout moment extracted from the video.",
                "viral_reason": "High-retention emotional storytelling triggers immediate curiosity and watch-through.",
                "commentary": "Analytical breakdown highlighting key narrative turning points.",
                "subtitle_highlights": ["HIGHLIGHT", "KEY", "MOMENT", "LESSON"],
                "broll_cues": ["key moment visualization", "emotional reaction closeup", "context establishing shot"],
                "sfx_cues": ["0s: attention whoosh", "3s: discovery ding", "20s: uplifting build", "End: resolved outro"],
                "tiktok_hashtags": "#trending #viral #shorts #fyp #interesting #mustwatch",
                "fb_cta": "What's your take on this? Let us know in the comments below! 👇",
            }

        moment_types = cfg["moment_types"]
        target_clip_len = 54.0 if (video_duration <= 0 or video_duration >= 55.0) else max(15.0, video_duration - 1.0)
        default_start = min(10.0, max(0.0, (video_duration - target_clip_len) * 0.1)) if video_duration > target_clip_len else 0.0
        default_end = min(video_duration if video_duration > 0 else 60.0, default_start + target_clip_len)
        if default_end - default_start < 15.0:
            default_end = default_start + 15.0

        niche_name = niche_profile.get("name", "YouTube Shorts")
        tiktok_hashtags = cfg["tiktok_hashtags"]
        
        # Tiêu đề 100% tiếng Anh chuẩn viral, không dùng lại tên video gốc
        english_title = self._ensure_viral_english_title("", niche_id, segment_idx=1)

        return [
            {
                "segment_id": 1,
                "moment_type": moment_types[0],
                "title": english_title,
                "start_seconds": round(default_start, 1),
                "end_seconds": round(default_end, 1),
                "duration_seconds": round(default_end - default_start, 1),
                "hook_quote": cfg["hook_quote"],
                "hook_options": cfg["hook_options"],
                "summary": cfg["summary"],
                "viral_score": 88,
                "viral_reason": cfg["viral_reason"],
                "safe_commentary_angle": cfg["commentary"],
                "edit_blueprint": {
                    "timeline_steps": [
                        f"00:00 - 00:03 | Opening Hook (High Energy): Zoom in 1.15x, Bold subtitle highlight, SFX: {cfg['sfx_cues'][0]}",
                        f"00:03 - 00:15 | Tension Build: Fast pacing 2.5s cut, B-roll: {cfg['broll_cues'][0]}",
                        f"00:15 - 00:{int(default_end - default_start) - 5:02d} | Climax Reveal: {cfg['broll_cues'][1] if len(cfg['broll_cues']) > 1 else 'key moment'}",
                        f"00:{int(default_end - default_start) - 5:02d} - End | Seamless Loop & CTA: Subscribe for more daily stories!"
                    ],
                    "broll_cues": cfg["broll_cues"],
                    "subtitle_highlights": cfg["subtitle_highlights"],
                    "audio_sfx_cues": cfg["sfx_cues"],
                },
                "omnichannel_pack": {
                    "tiktok_reels_caption": f"{english_title} ✨ {tiktok_hashtags}",
                    "facebook_post": cfg["fb_cta"],
                    "youtube_pinned_comment": f"📌 Watch the full story and support our journey: [Channel Link]\n\n🛒 Featured Gear (Store ID: kizakawa-20): https://www.amazon.com/dp/B004KM82IQ?tag=kizakawa-20"
                },
                "transcript_snippet": "Standout high-retention sequence from the source video."
            }
        ]

    def analyze_viral_moments(
        self,
        transcript_entries: List[Dict[str, Any]],
        video_title: str,
        video_duration: float,
        niche_id: str = "general",
        mode: str = "self_owned",
        channel_name: str = "",
        video_description: str = "",
        target_lang: str = "en",
    ) -> List[Dict[str, Any]]:
        """
        Gửi transcript và ngữ cảnh video sang Gemini AI để phân tích 3-5 đoạn cao trào nhất.
        ĐẶC BIỆT:
        - Tiêu đề (title) BẮT BUỘC 100% TIẾNG ANH chuyên nghiệp chuẩn Native Speaker.
        - TUYỆT ĐỐI KHÔNG dịch lại hoặc lấy lại tiêu đề gốc.
        - Toàn bộ hook, summary, caption BẮT BUỘC bằng Tiếng Anh khi kênh mục tiêu là US/quốc tế (target_lang='en').
        - Khối mô tả & hashtags đa nền tảng chuẩn SEO quốc tế.
        """
        from app.services.niche_manager import build_script_prompt_instructions

        niche_profile = get_niche(niche_id)
        niche_name = niche_profile.get("name", "General Viral")
        target_audience = niche_profile.get("target_audience", "Global YouTube Shorts Viewers")

        # ── Niche DNA Blueprint injection ──────────────────────────────────────
        niche_blueprint_instructions = build_script_prompt_instructions(niche_profile, language=target_lang or "en")

        # ── Lấy defaults theo niche để dùng trong validation ──────────────────
        niche_cfg = self._NICHE_FALLBACK_CFG.get(niche_id, {})
        default_moment_type = niche_cfg.get("moment_types", ["✨ VIRAL MOMENT"])[0]
        default_subtitle_highlights = niche_cfg.get("subtitle_highlights", ["HIGHLIGHT", "KEY", "MOMENT", "LESSON"])
        default_broll_cues = niche_cfg.get("broll_cues", ["key moment visualization", "emotional reaction closeup"])
        default_sfx_cues = niche_cfg.get("sfx_cues", ["0s: attention whoosh", "3s: discovery ding"])
        default_hook_options = niche_cfg.get("hook_options", [
            {"type": "Curiosity", "hook": "This single moment changed everything..."},
            {"type": "Emotional", "hook": "You won't believe what happened right after this!"},
            {"type": "Question",  "hook": "Would you have done the exact same thing?"}
        ])

        timeline_text_lines = []
        for entry in transcript_entries:
            start_m = int(entry['start'] // 60)
            start_s = int(entry['start'] % 60)
            timeline_text_lines.append(f"[{start_m:02d}:{start_s:02d}] {entry['text']}")
        timeline_str = "\n".join(timeline_text_lines[:500])

        # Nếu không có transcript, cung cấp ghi chú cho Gemini
        if not transcript_entries:
            desc_hint = f"and description: '{video_description[:400]}' " if video_description else ""
            timeline_str = (
                f"[NOTE: No automatic transcript available. "
                f"Based on original title '{video_title}', duration {video_duration:.0f}s {desc_hint}and "
                f"the Niche DNA '{niche_name}', creatively infer the most high-tension standout moments.]"
            )

        fair_use_guidance = (
            "- MODE: 'Transformative Fair-Use Commentary'. Provide analytical depth, new perspective, and transformative value."
            if mode == "transformative_commentary"
            else
            "- MODE: 'Self-Owned Channel Repurposing'. Maximize retention, emotional resonance, and click-through to the full video."
        )

        desc_info = f"- Original Video Context/Summary: {video_description[:1200]}\n" if video_description else ""

        prompt = f"""
You are an Elite YouTube Shorts Viral Retention Director & Growth Strategist for top-tier international channels.
Your mission: Analyze the video details below and extract exactly 3 to 5 high-impact viral moments (50s - 58s each) optimized for YouTube Shorts retention and engagement.

CRITICAL DURATION & NARRATIVE COMPLETION (PREVENT CUT-OFFS):
1. DURATION: Each clip MUST be between 50.0 and 58.0 seconds long (the ultimate YouTube Shorts retention sweet spot).
2. COMPLETE NARRATIVE ARC: Every clip must feel COMPLETE and SATISFYING. It MUST start at the beginning of a sentence/thought and finish cleanly at the end of a complete sentence (ending in '.', '!', or '?').
3. NEVER CUT MID-SENTENCE OR MID-WORD: Avoid abrupt, frustrating cutoffs. The viewer should feel amazed and want to subscribe, NOT annoyed because someone stopped mid-word.
4. "start_seconds" and "end_seconds" must yield a duration of 50 to 58 seconds (end_seconds - start_seconds >= 50.0 and <= 58.0).

CRITICAL VISUAL DIRECTIVE (ABSOLUTELY ZERO "TALKING HEAD" SIT-DOWN INTERVIEWS):
1. NEVER select timestamps where a person is sitting in a chair, room, or studio talking to an interviewer!
2. On YouTube Shorts / TikTok, talking heads in TV documentaries cause an immediate 90% swipe-away rate.
3. ONLY select timestamps featuring RAW ON-SCENE ACTION:
   - For Animal Rescue / Pets: Active searching, crawling under foundations, reaching for animals, mother dog pacing, shivering puppies, hands-on physical rescue.
   - For Wildlife / Nature: Close-ups of animals, chasing, physical drama, real-time field tension.
   - The visual MUST be active, physical, high-motion field footage. If speech occurs, it must accompany field action B-roll!

CRITICAL TITLE REQUIREMENTS (STRICT ENFORCEMENT):
1. The "title" MUST be written in 100% NATIVE, HIGHLY COMPELLING, PROFESSIONAL VIRAL ENGLISH.
2. DO NOT copy or directly translate the original video title. Create an entirely NEW, independent, clickable English hook title!
3. FORBIDDEN: NEVER output Vietnamese words or characters in the title, summary, or captions under any circumstance.
4. Title length: 45 to 65 characters. High curiosity gap or emotional trigger. Must end with #Shorts.
   Examples of high-converting titles:
   - For Animal Rescue: "He Refused to Let Go Until This Happened #Shorts", "The Moment He Realized He Was Finally Safe #Shorts"
   - For Wildlife: "The Deadliest 0.3 Seconds in the Wild #Shorts", "Why Even Lions Fear This Solitary Beast #Shorts"
   - For Psychology: "3 Micro-Expressions That Expose a Toxic Liar #Shorts", "The Psychological Trap 99% Fall For #Shorts"
   - For Ocean/Mysteries: "The Classified Signal 11,000m Below Sea Level #Shorts", "Science Still Cannot Explain What Happened Here #Shorts"

VIDEO INFORMATION:
- Original Title: {video_title}
- Duration: {video_duration:.1f} seconds
- Niche: {niche_name}
- Target Audience: {target_audience}
- Channel Brand: {channel_name or 'Brand Channel'}
{desc_info}{fair_use_guidance}
{niche_blueprint_instructions}

TRANSCRIPT WITH TIMESTAMPS:
\"\"\"
{timeline_str}
\"\"\"

OUTPUT MUST BE A STRICT JSON ARRAY ONLY (No markdown formatting around json, no preamble):
[
  {{
    "segment_id": 1,
    "moment_type": "{default_moment_type}",
    "title": "A Brand New 100% Native Viral English Title #Shorts",
    "start_seconds": 12.0,
    "end_seconds": 66.0,
    "duration_seconds": 54.0,
    "hook_quote": "Opening 3-second hook quote in natural English",
    "hook_options": [
      {{"type": "Emotional", "hook": "Option 1 hook formula"}},
      {{"type": "Curiosity", "hook": "Option 2 hook formula"}},
      {{"type": "Tension",   "hook": "Option 3 hook formula"}}
    ],
    "summary": "1-sentence English summary of what makes this clip unforgettable",
    "viral_score": 95,
    "viral_reason": "Psychological trigger explaining why viewers will rewatch",
    "safe_commentary_angle": "Transformative commentary angle adding unique value",
    "edit_blueprint": {{
      "timeline_steps": [
        "00:00 - 00:03 | Hook opening: Zoom in 1.15x, Bold subtitle highlight",
        "00:03 - 00:20 | Emotion / Tension build: 2.5s pacing, B-roll: {default_broll_cues[0] if default_broll_cues else 'key moment'}",
        "00:20 - 00:48 | Climax moment: SFX: {default_sfx_cues[2] if len(default_sfx_cues) > 2 else 'dramatic swell'}",
        "00:48 - 00:54 | Resolution & Subscribe CTA"
      ],
      "broll_cues": {default_broll_cues},
      "subtitle_highlights": {default_subtitle_highlights},
      "audio_sfx_cues": {default_sfx_cues}
    }},
    "omnichannel_pack": {{
      "tiktok_reels_caption": "Engaging English TikTok caption with hashtags",
      "facebook_post": "Community engagement Facebook post in English",
      "youtube_pinned_comment": "📌 Watch the full story and support our journey: [Channel Link]"
    }},
    "transcript_snippet": "Full transcript dialogue in this segment"
  }}
]
"""
        try:
            raw_response = self.content_agent._call_api(prompt, json_mode=True, timeout=40)
            clean_json = raw_response.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            clean_json = clean_json.strip()

            moments = json.loads(clean_json)
            if isinstance(moments, list) and len(moments) > 0:
                validated_moments = []
                for idx, m in enumerate(moments):
                    raw_s = max(0.0, float(m.get("start_seconds", 0)))
                    raw_e = float(m.get("end_seconds", raw_s + 54.0))

                    # BẮT DÍNH BIÊN CÂU THOẠI & KHÓA ĐỘ DÀI 48s - 58s (Sweet Spot Shorts)
                    s, e = self.snap_to_sentence_boundary(
                        transcript_entries=transcript_entries,
                        target_start=raw_s,
                        target_end=raw_e,
                        video_duration=video_duration,
                        target_min_duration=48.0,
                        target_max_duration=58.0
                    )
                    dur = round(e - s, 1)

                    m["segment_id"] = m.get("segment_id", idx + 1)
                    m["start_seconds"] = round(s, 1)
                    m["end_seconds"] = round(e, 1)
                    m["duration_seconds"] = dur
                    m["viral_score"] = int(m.get("viral_score", 90))
                    
                    # Chuẩn hóa tiêu đề: BẮT BUỘC 100% TIẾNG ANH CHUYÊN NGHIỆP
                    raw_t = m.get("title", "")
                    m["title"] = self._ensure_viral_english_title(raw_t, niche_id, segment_idx=idx + 1)

                    # Trích xuất lời thoại chính xác trong đoạn đã bắt dính
                    snippet_lines = []
                    timed_transcript = []
                    for entry in transcript_entries:
                        e_st = float(entry.get("start", 0))
                        if s <= e_st <= e:
                            clean_t = SubtitleHelper.sanitize_subtitle_text(entry.get("text", ""))
                            if clean_t:
                                snippet_lines.append(clean_t)
                                timed_transcript.append({
                                    "start": round(e_st, 2),
                                    "duration": round(float(entry.get("duration", 0)), 2),
                                    "text": clean_t,
                                })
                    if snippet_lines:
                        m["transcript_snippet"] = " ".join(snippet_lines)
                    m["timed_transcript"] = timed_transcript

                    m["moment_type"] = m.get("moment_type", default_moment_type)

                    # Đảm bảo cấu trúc hook_options (niche-aware defaults)
                    if not m.get("hook_options") or not isinstance(m.get("hook_options"), list):
                        m["hook_options"] = default_hook_options


                    # Khử rò rỉ tiếng Việt trong summary & viral reason nếu nhắm thị trường Mỹ / Quốc tế
                    vietnamese_regex = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]', re.IGNORECASE)
                    if (target_lang or "en").startswith("en"):
                        if m.get("summary") and vietnamese_regex.search(m["summary"]):
                            m["summary"] = niche_cfg.get("summary", "Standout high-retention highlight extracted from the video.")
                        if m.get("viral_reason") and vietnamese_regex.search(m["viral_reason"]):
                            m["viral_reason"] = niche_cfg.get("viral_reason", "High-tension emotional hook drives strong completion rate.")
                        if m.get("hook_quote") and vietnamese_regex.search(m["hook_quote"]):
                            m["hook_quote"] = niche_cfg.get("hook_quote", "Watch what happens right here...")

                    # Đảm bảo cấu trúc edit_blueprint (niche-aware defaults)
                    bp = m.get("edit_blueprint")
                    if not isinstance(bp, dict):
                        bp = {}
                    if not bp.get("timeline_steps"):
                        if (target_lang or "en").startswith("en"):
                            bp["timeline_steps"] = [
                                f"00:00 - 00:03 | Opening Hook ({niche_name}): Zoom in 1.15x, SFX: {default_sfx_cues[0] if default_sfx_cues else 'attention sfx'}",
                                f"00:03 - 00:15 | Emotion / Tension Build: Fast 2.5s pacing, B-roll: {default_broll_cues[0] if default_broll_cues else 'key moment'}",
                                f"00:15 - 00:{max(0, int(dur) - 5):02d} | Climax Reveal: SFX: {default_sfx_cues[2] if len(default_sfx_cues) > 2 else 'climax sfx'}",
                                f"00:{max(0, int(dur) - 5):02d} - {int(dur):02d}s | Seamless Infinite Loop & Retention CTA!"
                            ]
                        else:
                            bp["timeline_steps"] = [
                                f"00:00 - 00:03 | Hook mở đầu phù hợp tone {niche_name}: Zoom in 1.15x, SFX: {default_sfx_cues[0] if default_sfx_cues else 'attention sfx'}",
                                f"00:03 - 00:15 | Xây dựng cảm xúc {niche_name}: Cắt cảnh 2.5s, B-roll: {default_broll_cues[0] if default_broll_cues else 'key b-roll'}",
                                f"00:15 - 00:{max(0, int(dur) - 5):02d} | Cao trào phù hợp ngách, SFX: {default_sfx_cues[2] if len(default_sfx_cues) > 2 else 'climax sfx'}",
                                f"00:{max(0, int(dur) - 5):02d} - {int(dur):02d}s | CTA Loop phù hợp cộng đồng {niche_name}!"
                            ]
                    if not bp.get("broll_cues"):
                        bp["broll_cues"] = default_broll_cues
                    if not bp.get("subtitle_highlights"):
                        bp["subtitle_highlights"] = default_subtitle_highlights
                    if not bp.get("audio_sfx_cues"):
                        bp["audio_sfx_cues"] = default_sfx_cues
                    m["edit_blueprint"] = bp

                    # Đảm bảo cấu trúc omnichannel_pack (niche-aware defaults)
                    op = m.get("omnichannel_pack")
                    if not isinstance(op, dict):
                        op = {}
                    title_clean = m.get("title", video_title)[:50]
                    niche_hashtags = niche_cfg.get("tiktok_hashtags", "#shorts #viral #fyp")
                    if not op.get("tiktok_reels_caption"):
                        op["tiktok_reels_caption"] = f"{title_clean} ✨ {niche_hashtags}"
                    if not op.get("facebook_post"):
                        if (target_lang or "en").startswith("en"):
                            op["facebook_post"] = niche_cfg.get("fb_cta",
                                f"{m.get('summary', title_clean)} What do you think about this? Drop your thoughts below! 👇")
                        else:
                            op["facebook_post"] = niche_cfg.get("fb_cta",
                                f"{m.get('summary', title_clean)} Bạn nghĩ sao? Chia sẻ cảm nhận bên dưới! 👇")
                    if not op.get("youtube_pinned_comment"):
                        from app.services.monetization_engine import build_repurposed_pinned_comment
                        op["youtube_pinned_comment"] = build_repurposed_pinned_comment(
                            title=m.get("title", video_title),
                            niche_id=niche_id,
                            source_url="[Link Video Gốc]",
                            channel_name=channel_name,
                            tracking_tag="kizakawa-20",
                        )
                    m["omnichannel_pack"] = op

                    validated_moments.append(m)
                return validated_moments
        except Exception as e:
            logger.error(f"Lỗi phân tích Gemini cho moments: {e}")

        # Niche-Aware Fallback: dùng _build_niche_fallback_moments thay vì hardcoded generic
        logger.warning(f"[Repurposer] Dùng niche-aware fallback cho niche='{niche_id}', video='{video_title[:40]}'")
        return self._build_niche_fallback_moments(niche_profile, video_title, video_duration)

    def _build_niche_fallback_mashups(
        self,
        niche_profile: Dict[str, Any],
        video_title: str,
        video_duration: float,
    ) -> List[Dict[str, Any]]:
        """Fallback đa phân đoạn thông minh tính toán theo tỷ lệ vàng thời lượng video."""
        dur = max(float(video_duration), 60.0)
        niche_id = niche_profile.get("id", "general")
        english_title = self._ensure_viral_english_title("", niche_id, segment_idx=1)

        # Cắt 4-5 lát cắt từ các mốc thời gian khác nhau tạo thành 1 chuỗi kịch tính
        h_start = min(dur * 0.72, dur - 6.0)
        s_start = max(2.0, dur * 0.05)
        t_start = min(dur * 0.38, dur - 18.0)
        c_start = min(dur * 0.78, dur - 14.0)
        l_start = min(dur * 0.90, dur - 4.5)

        clips = [
            {
                "clip_id": 1,
                "role": "HOOK",
                "context_badge": "🚨 [ COLD OPEN: THE CLIMAX ]",
                "start_seconds": round(h_start, 1),
                "end_seconds": round(h_start + 3.5, 1),
                "duration": 3.5,
                "zoom_punch": 1.15,
                "transition": "whoosh",
                "summary": "Shocking climax cold open hook",
                "transcript_snippet": "This single moment changed the entire outcome!",
            },
            {
                "clip_id": 2,
                "role": "SETUP",
                "context_badge": "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]",
                "start_seconds": round(s_start, 1),
                "end_seconds": round(s_start + 8.5, 1),
                "duration": 8.5,
                "zoom_punch": 1.0,
                "transition": "cut",
                "summary": "Origin backstory & setup",
                "transcript_snippet": "It all started under the most unexpected conditions.",
            },
            {
                "clip_id": 3,
                "role": "TENSION",
                "context_badge": "⏳ [ SEARCHING DEEP IN THE DARK ]",
                "start_seconds": round(t_start, 1),
                "end_seconds": round(t_start + 16.0, 1),
                "duration": 16.0,
                "zoom_punch": 1.12,
                "transition": "whoosh",
                "summary": "Escalating conflict and rising stakes",
                "transcript_snippet": "With every passing second, the stakes reached the breaking point.",
            },
            {
                "clip_id": 4,
                "role": "CLIMAX",
                "context_badge": "🎯 [ THE MIRACLE BREAKTHROUGH ]",
                "start_seconds": round(c_start, 1),
                "end_seconds": round(c_start + 14.0, 1),
                "duration": 14.0,
                "zoom_punch": 1.08,
                "transition": "whoosh",
                "summary": "The dramatic revelation & impact",
                "transcript_snippet": "And right when failure seemed certain, the truth broke through.",
            },
            {
                "clip_id": 5,
                "role": "LOOP",
                "context_badge": "🔁 [ AND THE MIRACLE LOOPS... ]",
                "start_seconds": round(l_start, 1),
                "end_seconds": round(l_start + 4.0, 1),
                "duration": 4.0,
                "zoom_punch": 1.15,
                "transition": "whoosh",
                "summary": "Seamless loop connector back to Hook",
                "transcript_snippet": "Leading straight back to that very first mistake...",
            },
        ]

        total_d = sum(c["duration"] for c in clips)
        return [
            {
                "mashup_id": 1,
                "title": f"The Untold Truth Behind {video_title[:35]}! 🤯",
                "hook_sentence": clips[0]["transcript_snippet"],
                "story_arc": "Cold Open Climax (0-3s) ➔ Backstory Setup (3-12s) ➔ Escalation (12-28s) ➔ Climax Payoff (28-42s) ➔ Infinite Loop (42-46s)",
                "viral_score": 96,
                "total_duration": round(total_d, 1),
                "bgm_mood": "suspense",
                "summary": "High-octane multi-clip montage combining the highest tension moments into a non-linear viral narrative.",
                "pinned_comment": "Would you have seen this coming? Tell me below! 👇",
                "description": f"Watch the craziest moments from {video_title}! #shorts #viral",
                "clips": clips,
            }
        ]

    def analyze_viral_mashups(
        self,
        transcript_entries: List[Dict[str, Any]],
        video_title: str,
        video_duration: float,
        niche_id: str = "general",
        mode: str = "self_owned",
        channel_name: str = "",
        video_description: str = "",
        target_lang: str = "en",
    ) -> List[Dict[str, Any]]:
        """
        AI Mashup Storyboard: Tái chế video dạng ghép chuỗi đa phân đoạn (Multi-Clip Mashup).
        Lấy cảm hứng từ Auto-Editor và VideoCutterAI.
        """
        from app.services.niche_manager import get_niche

        niche_profile = get_niche(niche_id)
        niche_name = niche_profile.get("name", "General Viral")

        timeline_lines = []
        for entry in transcript_entries:
            sm = int(entry['start'] // 60)
            ss = int(entry['start'] % 60)
            timeline_lines.append(f"[{sm:02d}:{ss:02d}] {entry['text']}")
        timeline_str = "\n".join(timeline_lines[:600])

        if not transcript_entries:
            timeline_str = (
                f"[Video '{video_title}', duration {video_duration:.0f}s. "
                f"Description: '{video_description[:400]}'. Niche: '{niche_name}'. "
                f"Synthesize high-potential viral mashup cut sequences.]"
            )

        prompt = f"""You are an Elite Viral Shorts Director & Master Video Montage Editor.
Your mission: Analyze the transcript and timeline of this long video and create exactly 2 distinct, high-impact "MULTI-CLIP MASHUP STORYBOARDS" (35s to 55s total duration each).

🚨 CRITICAL DIRECTIVE: ZERO TALKING HEADS & MANDATORY TIMELINE CONTEXT BADGES!
1. ABSOLUTELY ZERO SIT-DOWN INTERVIEW CUTAWAYS:
   - In TV documentaries (e.g. Animal Planet, Discovery), interviewees sit in a room/chair talking to a camera.
   - NEVER select timestamps showing a person sitting in a chair, room, or vehicle talking to an interviewer!
   - 100% of chosen clips MUST BE RAW ON-SCENE PHYSICAL ACTION:
     - Searching dark crawlspaces, crawling under foundations, flashlights waving.
     - Mother dog anxious, shivering whimpering puppies, hands reaching, pulling animals to safety.
     - Real-time on-site field camera movement and emotional suspense.
2. TIMELINE CONTEXT BADGES ("context_badge"):
   - For every clip, provide a punchy context badge explaining the timeline/chapter so viewers are never confused when time rewinds or jumps:
     - HOOK: e.g. "🚨 [ THE RESCUE AT 2:00 AM ]"
     - SETUP: e.g. "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]"
     - TENSION: e.g. "⏳ [ SEARCHING DEEP UNDER HOUSE ]"
     - CLIMAX: e.g. "🎯 [ THE MIRACLE BREAKTHROUGH ]"
     - LOOP: e.g. "🔁 [ AND THE MIRACLE LOOPS... ]"

A "Mashup Storyboard" consists of 3 to 5 NON-CONTIGUOUS micro-clips chosen from different parts of the video, dynamically re-sequenced into a viral narrative:
- Clip 1 (HOOK, ~3-4s): The most shocking, paradoxical, or high-stakes moment (ideally chosen from the CLIMAX or end of the video) to stop the scroll.
- Clip 2 (SETUP, ~6-10s): The backstory / context of how this started.
- Clip 3 (TENSION / ACTION, ~10-18s): The rising conflict or attempt.
- Clip 4 (CLIMAX / RESOLUTION, ~10-16s): The big reveal, impact, or emotional payoff.
- Clip 5 (SEAMLESS_LOOP, ~3-5s): A punchy phrase that links seamlessly back to the opening Hook (Clip 1) for infinite looping.

CRITICAL REQUIREMENTS:
1. "start_seconds" and "end_seconds" for each clip must be realistic timestamps within 0.0 and {video_duration:.1f}s.
2. Sum of clip durations in each mashup MUST be between 35.0 and 55.0 seconds.
3. Every title must be native English, click-worthy, under 60 characters.
4. Each clip must have "zoom_punch" (1.15 to 1.25) and a descriptive "context_badge".

[VIDEO DETAILS]
Title: {video_title}
Duration: {video_duration:.0f}s
Niche: {niche_name}

[TIMELINE / TRANSCRIPT]
{timeline_str}

Return ONLY a valid JSON list matching this schema:
[
  {{
    "mashup_id": 1,
    "title": "High CTR Click-Worthy Title (under 60 chars)",
    "hook_sentence": "The punchy opening line of Clip 1",
    "story_arc": "Cold Open Climax -> Backstory -> Escalation -> Resolution -> Infinite Loop",
    "viral_score": 96,
    "total_duration": 48.0,
    "bgm_mood": "suspense",
    "summary": "1-sentence summary of why this mashup goes viral",
    "pinned_comment": "Curiosity provoking pinned comment to spark debate",
    "description": "Engaging description with #shorts #viral hashtags",
    "clips": [
      {{
        "clip_id": 1,
        "role": "HOOK",
        "context_badge": "🚨 [ COLD OPEN: THE CLIMAX ]",
        "start_seconds": 180.2,
        "end_seconds": 183.5,
        "duration": 3.3,
        "summary": "Visual description of action in this clip",
        "transcript_snippet": "Spoken text in this clip",
        "zoom_punch": 1.22,
        "transition": "whoosh"
      }},
      {{
        "clip_id": 2,
        "role": "SETUP",
        "context_badge": "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]",
        "start_seconds": 12.0,
        "end_seconds": 19.5,
        "duration": 7.5,
        "summary": "Backstory visual",
        "transcript_snippet": "Spoken text in setup",
        "zoom_punch": 1.18,
        "transition": "cut"
      }},
      {{
        "clip_id": 3,
        "role": "TENSION",
        "context_badge": "⏳ [ SEARCHING DEEP IN THE DARK ]",
        "start_seconds": 140.0,
        "end_seconds": 156.0,
        "duration": 16.0,
        "summary": "Intense escalation",
        "transcript_snippet": "Spoken text in tension",
        "zoom_punch": 1.20,
        "transition": "whoosh"
      }},
      {{
        "clip_id": 4,
        "role": "CLIMAX",
        "context_badge": "🎯 [ THE MIRACLE BREAKTHROUGH ]",
        "start_seconds": 184.0,
        "end_seconds": 198.0,
        "duration": 14.0,
        "summary": "The dramatic payoff",
        "transcript_snippet": "Spoken text in climax",
        "zoom_punch": 1.25,
        "transition": "whoosh"
      }},
      {{
        "clip_id": 5,
        "role": "LOOP",
        "context_badge": "🔁 [ AND THE MIRACLE LOOPS... ]",
        "start_seconds": 210.0,
        "end_seconds": 214.2,
        "duration": 4.2,
        "summary": "Loop back to start",
        "transcript_snippet": "Leading straight back to where it all began...",
        "zoom_punch": 1.20,
        "transition": "whoosh"
      }}
    ]
  }}
]
"""
        try:
            raw_res = self.content_agent._call_api(prompt, json_mode=True, max_total_seconds=50)
            cleaned = re.sub(r'^```json\s*', '', raw_res.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            data = json.loads(cleaned)
            if isinstance(data, list) and len(data) > 0:
                validated_mashups = []
                for idx, m in enumerate(data, 1):
                    if not isinstance(m, dict):
                        continue
                    m["mashup_id"] = idx
                    clips = m.get("clips", [])
                    if not isinstance(clips, list) or len(clips) < 2:
                        continue
                    valid_clips = []
                    for c_idx, c in enumerate(clips, 1):
                        st = max(0.0, float(c.get("start_seconds", 0.0)))
                        et = float(c.get("end_seconds", st + 4.0))
                        dur = max(0.8, et - st)

                        # Match exact transcript segments for real spoken words
                        c_snippet_lines = []
                        c_timed = []
                        if transcript_entries:
                            for entry in transcript_entries:
                                e_st = float(entry.get("start", 0))
                                if st <= e_st <= et:
                                    clean_t = SubtitleHelper.sanitize_subtitle_text(entry.get("text", ""))
                                    if clean_t:
                                        c_snippet_lines.append(clean_t)
                                        c_timed.append({
                                            "start": round(e_st, 2),
                                            "duration": round(float(entry.get("duration", 0)), 2),
                                            "text": clean_t,
                                        })
                        if c_snippet_lines:
                            transcript_snippet_val = " ".join(c_snippet_lines)
                        else:
                            transcript_snippet_val = c.get("transcript_snippet") or c.get("text") or c.get("summary") or ""

                        valid_clips.append({
                            "clip_id": c_idx,
                            "role": c.get("role", "CLIP"),
                            "context_badge": c.get("context_badge") or "",
                            "start_seconds": round(st, 1),
                            "end_seconds": round(et, 1),
                            "duration": round(dur, 1),
                            "summary": c.get("summary", ""),
                            "transcript_snippet": transcript_snippet_val,
                            "timed_transcript": c_timed,
                            "zoom_punch": float(c.get("zoom_punch", 1.20)),
                            "transition": c.get("transition", "whoosh"),
                        })
                    m["clips"] = valid_clips
                    m["total_duration"] = round(sum(c["duration"] for c in valid_clips), 1)
                    if not m.get("bgm_mood"):
                        m["bgm_mood"] = "suspense"
                    validated_mashups.append(m)

                if validated_mashups:
                    return validated_mashups
        except Exception as e:
            logger.error(f"[Repurposer] Lỗi phân tích Gemini cho Mashups: {e}")

        logger.warning(f"[Repurposer] Dùng niche fallback mashup cho '{video_title[:35]}'")
        return self._build_niche_fallback_mashups(niche_profile, video_title, video_duration)
