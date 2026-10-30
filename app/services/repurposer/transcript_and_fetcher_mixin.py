"""app.services.repurposer.transcript_and_fetcher_mixin — Tải thông tin video, phụ đề, cắt câu & tải phân đoạn YouTube."""
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



class TranscriptAndFetcherMixin:
    def __init__(self):
        self.content_agent = ContentAgent()

    def get_video_metadata(self, url_or_path: str) -> Dict[str, Any]:
        """Lấy thông tin tiêu đề, thời lượng, tác giả từ link YouTube hoặc file MP4 cục bộ."""
        if os.path.exists(url_or_path):
            file_path = os.path.abspath(url_or_path)
            info = probe_file(file_path)
            duration_s = 0.0
            if info and "format" in info:
                duration_s = float(info["format"].get("duration", 0))
            return {
                "source_type": "local_file",
                "title": Path(file_path).stem.replace("_", " ").title(),
                "duration_seconds": round(duration_s, 2),
                "author": "Local Workspace",
                "video_path": file_path,
                "video_id": Path(file_path).stem,
            }
        return self.get_video_info(url_or_path)

    @staticmethod
    def _find_cookie_file() -> Optional[str]:
        """Tìm file cookies.txt của YouTube nếu có trong dự án hoặc cấu hình."""
        cookie_candidates = [
            Path("cookies.txt"),
            Path("youtube_cookies.txt"),
            Path("config/cookies.txt"),
            Path("config/youtube_cookies.txt"),
            Path("assets/cookies.txt"),
        ]
        import os
        env_cookie = os.getenv("YOUTUBE_COOKIES_PATH")
        if env_cookie:
            cookie_candidates.insert(0, Path(env_cookie))

        for candidate in cookie_candidates:
            if candidate.exists() and candidate.stat().st_size > 10:
                return str(candidate.resolve())
        return None

    @classmethod
    def _build_yt_dlp_opts(cls, base_opts: Optional[Dict[str, Any]] = None, client_combo: str = "ios_android") -> Dict[str, Any]:
        """
        Xây dựng cấu hình yt-dlp tối ưu chống chặn 'Sign in to confirm you're not a bot' từ YouTube:
        1. Sử dụng player_client di động (ios, android, mweb) để bỏ qua kiểm tra token bot trên web.
        2. Tự động nạp cookies.txt nếu có.
        3. Tự động fallback linh hoạt các định dạng MP4.
        """
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
        }
        if client_combo == "ios_android":
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["ios", "android", "mweb"],
                    "player_skip": ["webpage", "configs"],
                }
            }
        elif client_combo == "android_first":
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["android", "ios"],
                }
            }
        elif client_combo == "mweb_first":
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["mweb", "android"],
                }
            }

        cookie_path = cls._find_cookie_file()
        if cookie_path:
            opts["cookiefile"] = cookie_path

        if base_opts:
            for k, v in base_opts.items():
                opts[k] = v

        return opts

    def get_video_info(self, url_or_path: str) -> Dict[str, Any]:
        """Lấy metadata thông tin video từ đường dẫn local hoặc link YouTube (Bypass Bot-Check)."""
        file_path = Path(url_or_path)
        if file_path.exists():
            return {
                "source_type": "local",
                "title": file_path.stem,
                "duration_seconds": self._get_duration(str(file_path)),
                "author": "Local File",
                "thumbnail": "",
                "video_id": Path(file_path).stem,
            }

        from app.services.media.bilibili_fetcher import (
            get_bilibili_video_info_and_streams,
            is_bilibili_source,
        )
        if is_bilibili_source(url_or_path):
            b_info = get_bilibili_video_info_and_streams(url_or_path)
            if b_info:
                return {
                    "source_type": "bilibili",
                    "title": b_info.get("title", f"Bilibili Video {b_info.get('bvid')}"),
                    "duration_seconds": float(b_info.get("duration", 0.0)),
                    "author": b_info.get("uploader", "Bilibili Creator"),
                    "thumbnail": b_info.get("thumbnail", ""),
                    "video_id": b_info.get("bvid") or b_info.get("id"),
                    "webpage_url": b_info.get("webpage_url", url_or_path),
                    "description": b_info.get("description", "") or "",
                }

        video_id = extract_youtube_video_id(url_or_path)
        if not video_id:
            raise ValueError(f"Không thể nhận diện Video ID hoặc đường dẫn file: {url_or_path}")

        try:
            import yt_dlp
            info = None
            last_err = None
            for combo in ["ios_android", "android_first", "mweb_first"]:
                try:
                    ydl_opts = self._build_yt_dlp_opts(
                        base_opts={"skip_download": True},
                        client_combo=combo
                    )
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                        if info:
                            break
                except Exception as ex:
                    last_err = ex
                    continue

            if not info and last_err:
                raise last_err

            return {
                "source_type": "youtube",
                "title": info.get("title", f"YouTube Video {video_id}"),
                "duration_seconds": float(info.get("duration", 0)),
                "author": info.get("uploader", "YouTube Creator"),
                "thumbnail": info.get("thumbnail", f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"),
                "video_id": video_id,
                "webpage_url": info.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                "description": info.get("description", "") or "",
            }
        except Exception as e:
            logger.warning(f"Lỗi khi lấy metadata yt-dlp cho {video_id}: {e}")
            return {
                "source_type": "youtube",
                "title": f"YouTube Video ({video_id})",
                "duration_seconds": 600.0,
                "author": "YouTube Channel",
                "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                "video_id": video_id,
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "description": "",
            }

    def fetch_transcript(self, video_id: str, target_lang: str = "en") -> List[Dict[str, Any]]:
        """
        Lấy phụ đề / transcript kèm mốc thời gian từ YouTube.
        - Mặc định ưu tiên Tiếng Anh (en, en-US, en-GB, en-CA, en-AU) cho thị trường Mỹ và kênh quốc tế.
        - Chỉ ưu tiên tiếng Việt khi target_lang == 'vi'.
        - Tự động ưu tiên phụ đề do người tạo (manual transcript) trước phụ đề tự động (auto-generated).
        - Nếu video không có tiếng Anh, thử dùng bản dịch transcript sang tiếng Anh nếu có.
        - Khử hoàn toàn các thẻ âm thanh rác: [Music], [Âm nhạc], [Applause]...
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            ytt = YouTubeTranscriptApi()
            transcript_list = ytt.list(video_id)
            
            clean_target = (target_lang or "en").strip().lower()
            if clean_target.startswith("vi"):
                target_langs = ['vi', 'en']
            else:
                target_langs = ['en', 'en-US', 'en-GB', 'en-CA', 'en-AU']

            selected_transcript = None

            # 1. Thử tìm phụ đề thủ công do người làm video tải lên trước (Manual Transcripts)
            for lang in target_langs:
                try:
                    selected_transcript = transcript_list.find_manually_created_transcript([lang])
                    if selected_transcript:
                        logger.info(f"✅ [Repurposer] Tìm thấy manual transcript ({selected_transcript.language_code}) cho {video_id}")
                        break
                except Exception:
                    pass

            # 2. Thử tìm phụ đề tự động theo đúng ngôn ngữ mục tiêu (Generated Transcripts)
            if not selected_transcript:
                for lang in target_langs:
                    try:
                        selected_transcript = transcript_list.find_generated_transcript([lang])
                        if selected_transcript:
                            logger.info(f"✅ [Repurposer] Tìm thấy generated transcript ({selected_transcript.language_code}) cho {video_id}")
                            break
                    except Exception:
                        pass

            # 3. Thử tìm bất kỳ bản nào khớp với target_langs
            if not selected_transcript:
                for lang in target_langs:
                    try:
                        selected_transcript = transcript_list.find_transcript([lang])
                        if selected_transcript:
                            break
                    except Exception:
                        pass

            # 4. Nếu không có tiếng Anh nhưng có transcript khác, ưu tiên translate sang tiếng Anh nếu translatable
            if not selected_transcript and clean_target != "vi":
                for t in transcript_list:
                    if getattr(t, 'is_translatable', False):
                        try:
                            selected_transcript = t.translate('en')
                            logger.info(f"🌐 [Repurposer] Đã dịch tự động transcript ({t.language_code} -> en) cho {video_id}")
                            break
                        except Exception:
                            pass

            # 5. Fallback cuối cùng: lấy bất kỳ transcript nào có sẵn
            if not selected_transcript:
                for t in transcript_list:
                    selected_transcript = t
                    break

            if selected_transcript:
                raw_entries = selected_transcript.fetch()
                result = []
                noise_tags = {"[Music]", "[Âm nhạc]", "[Applause]", "[Laughter]", "[Sound]", "[Audio]", "[Tiếng vỗ tay]"}
                for item in raw_entries:
                    if isinstance(item, dict):
                        text = item.get("text", "").strip()
                        start_val = item.get("start", 0)
                        dur_val = item.get("duration", 0)
                    else:
                        text = getattr(item, "text", "")
                        if text: text = text.strip()
                        start_val = getattr(item, "start", 0)
                        dur_val = getattr(item, "duration", 0)

                    if text and text not in noise_tags:
                        result.append({
                            "start": round(float(start_val), 2),
                            "duration": round(float(dur_val), 2),
                            "text": text.replace("\n", " "),
                        })
                return result

        except Exception as e:
            logger.warning(f"Không lấy được transcript qua YouTubeTranscriptApi cho {video_id}: {e}")

        return []

    def snap_to_sentence_boundary(
        self,
        transcript_entries: List[Dict[str, Any]],
        target_start: float,
        target_end: float,
        video_duration: float,
        target_min_duration: float = 48.0,
        target_max_duration: float = 58.0,
    ) -> Tuple[float, float]:
        """
        Thuật toán Bắt Dính Biên Câu Thoại (Sentence Boundary Snapper):
        - Tự động bắt dính mốc bắt đầu vào đầu câu (sau dấu chấm hoặc khoảng lặng).
        - Dò tìm điểm kết thúc câu trọn vẹn (kết thúc bằng dấu '.', '!', '?' hoặc khoảng nghỉ thở).
        - Khóa cứng độ dài trong khoảng Vàng: 48.0s - 58.0s (triệt tiêu 100% tình trạng cắt ngang lời thoại).
        """
        if not transcript_entries or len(transcript_entries) == 0:
            t_start = max(0.0, float(target_start))
            max_possible_dur = min(target_max_duration, video_duration if video_duration > 0 else 58.0)
            t_end = min(video_duration if video_duration > 0 else 9999.0, t_start + max_possible_dur)
            if t_end - t_start < target_min_duration and video_duration >= target_min_duration:
                t_start = max(0.0, t_end - target_min_duration)
            return round(t_start, 2), round(t_end, 2)

        # 1. Tìm điểm bắt đầu (snapped_start):
        best_start = float(target_start)
        best_start_idx = 0
        min_dist_start = 999999.0

        for i, entry in enumerate(transcript_entries):
            e_start = float(entry.get("start", 0))
            dist = abs(e_start - target_start)

            # Kiểm tra xem có phải điểm mở đầu câu tự nhiên không
            is_natural_start = False
            if i == 0:
                is_natural_start = True
            else:
                prev_entry = transcript_entries[i - 1]
                prev_end = float(prev_entry.get("start", 0)) + float(prev_entry.get("duration", 0))
                prev_text = prev_entry.get("text", "").strip()
                # Khoảng lặng giữa 2 câu >= 0.35s hoặc câu trước kết thúc bằng dấu câu hoàn chỉnh
                if (e_start - prev_end >= 0.35) or (prev_text and prev_text[-1] in ".!?"):
                    is_natural_start = True

            if is_natural_start and dist < 7.0:
                if dist < min_dist_start:
                    min_dist_start = dist
                    best_start = e_start
                    best_start_idx = i
            elif dist < min_dist_start and min_dist_start > 6.0:
                min_dist_start = dist
                best_start = e_start
                best_start_idx = i

        best_start = max(0.0, round(best_start, 2))

        # 2. Tìm điểm kết thúc (snapped_end):
        # Quét các câu kết thúc sao cho duration nằm trong khoảng [best_start + 45s, best_start + 58.5s]
        candidate_ends = []

        for i in range(best_start_idx, len(transcript_entries)):
            entry = transcript_entries[i]
            e_start = float(entry.get("start", 0))
            e_dur = float(entry.get("duration", 0))
            e_end = e_start + e_dur
            dur_from_start = e_end - best_start

            if dur_from_start > target_max_duration + 0.5:
                break
            if video_duration > 0 and e_end > video_duration:
                break

            if dur_from_start >= 45.0:
                text = entry.get("text", "").strip()
                score = 0.0

                # Tiêu chí 1: Kết thúc bằng dấu câu hoàn chỉnh (. ! ?)
                if text and text[-1] in ".!?":
                    score += 100.0
                elif text and text[-1] in ",;:-":
                    score += 40.0

                # Tiêu chí 2: Khoảng lặng nghỉ thở sau câu
                if i + 1 < len(transcript_entries):
                    next_start = float(transcript_entries[i + 1].get("start", 0))
                    gap = next_start - e_end
                    if gap >= 0.5:
                        score += 60.0
                    elif gap >= 0.25:
                        score += 30.0
                else:
                    score += 80.0

                # Tiêu chí 3: Điểm ngọt tối ưu quanh 52s - 56s
                if 50.0 <= dur_from_start <= 56.5:
                    score += 50.0 - abs(dur_from_start - 54.0) * 4.0

                candidate_ends.append({
                    "end_seconds": e_end,
                    "score": score,
                    "duration": dur_from_start,
                    "text": text
                })

        if candidate_ends:
            candidate_ends.sort(key=lambda x: x["score"], reverse=True)
            best_candidate = candidate_ends[0]
            # Thêm 0.35s đệm âm thanh tự nhiên sau từ cuối cùng để không bị cụt âm
            best_end = round(best_candidate["end_seconds"] + 0.35, 2)
        else:
            target_len = min(54.0, target_max_duration)
            if video_duration > 0:
                best_end = min(video_duration, best_start + target_len)
            else:
                best_end = best_start + target_len

        # Khóa cứng trần thời lượng Shorts <= 58.0s
        if best_end - best_start > target_max_duration:
            best_end = best_start + target_max_duration
        if video_duration > 0 and best_end > video_duration:
            best_end = video_duration

        if best_end - best_start < 15.0 and video_duration >= 15.0:
            best_start = max(0.0, best_end - 15.0)

        return round(best_start, 2), round(best_end, 2)


    # ── 2. PHÂN TÍCH HIGHLIGHTS VIRAL BẰNG GEMINI AI ──────────────────────────

    # ── NICHE-AWARE FALLBACK CONFIG (100% NATIVE VIRAL ENGLISH) ───────────────
    _NICHE_FALLBACK_CFG: Dict[str, Dict[str, Any]] = {
        "animal_rescue": {
            "moment_types": ["🐾 MIRACLE RESCUE", "💖 UNCONDITIONAL LOVE", "🏠 A NEW BEGINNING"],
            "fallback_titles": [
                "He Refused to Let Go Until This Happened #Shorts",
                "The Moment This Abandoned Soul Realized He Was Safe #Shorts",
                "From Broken to Loved: An Incredible Miracle #Shorts"
            ],
            "hook_quote": "The moment he looked into their eyes, everything changed forever...",
            "hook_options": [
                {"type": "Emotional", "hook": "That single look in his eyes changed everything in an instant..."},
                {"type": "Hope",      "hook": "From total despair to pure joy — a miracle nobody saw coming!"},
                {"type": "Curiosity", "hook": "What happens when a broken soul experiences love for the very first time?"}
            ],
            "summary": "Heartwarming rescue and emotional transformation of an abandoned animal.",
            "viral_reason": "Authentic empathy and wholesome transformation triggers overwhelming dopamine and high retention.",
            "commentary": "Analysis of animal behavioral recovery and rescue transformation psychology.",
            "subtitle_highlights": ["RESCUED", "SURVIVED", "MIRACLE", "LOVE"],
            "broll_cues": ["rescuer hands holding trembling animal", "first meal after days starving", "animal running free in sunlit field"],
            "sfx_cues": ["0s: gentle heartbeat pulse", "3s: soft piano chime", "20s: orchestral swell of joy", "End: warm fade out"],
            "tiktok_hashtags": "#animallove #rescue #adoption #paws #miracle #heartwarming #shorts #fyp",
            "fb_cta": "This incredible transformation brought millions to tears. What would you have done? 🐾💖",
        },
        "wildlife_predators": {
            "moment_types": ["⚡ LETHAL PRECISION", "🦁 APEX HUNTER", "💀 RAW POWER"],
            "fallback_titles": [
                "The Deadliest 0.3 Seconds in the Animal Kingdom #Shorts",
                "Why Even Lions Fear This Solitary Hunter #Shorts",
                "Raw Power Unleashed: The Apex Strike #Shorts"
            ],
            "hook_quote": "In less than a second, nature revealed who truly rules this land...",
            "hook_options": [
                {"type": "Dramatic", "hook": "In just 0.3 seconds, the outcome was decided forever..."},
                {"type": "Impact",   "hook": "Fangs punching straight through solid bone like an empty tin can — nothing escapes!"},
                {"type": "Question", "hook": "Who actually sits at the absolute top of the food chain? Watch this!"}
            ],
            "summary": "Apex predator high-speed strike and lethal hunting biomechanics.",
            "viral_reason": "Raw adrenaline and primal survival reflex triggers intense watch-time.",
            "commentary": "Evolutionary biomechanics and predatory tactics breakdown.",
            "subtitle_highlights": ["APEX", "LETHAL", "SPEED", "POWER"],
            "broll_cues": ["slow motion predator sprint 1000fps", "eye-level macro predator eye closeup", "bone-crushing bite force demo"],
            "sfx_cues": ["0s: deep rumble growl", "3s: dramatic percussion hit", "20s: adrenaline surge strings", "End: predator roar"],
            "tiktok_hashtags": "#wildlife #apex #predator #nature #wildanimal #hunting #shorts #fyp",
            "fb_cta": "Raw nature at its most unforgiving. Did you expect that ending? 🦁⚡",
        },
        "psychology": {
            "moment_types": ["🧠 MIND CONTROL", "👁️ UNMASKED TRUTH", "⚠️ TOXIC BEHAVIOR"],
            "fallback_titles": [
                "3 Micro-Expressions That Expose a Chronic Liar #Shorts",
                "The Dangerous Psychological Trick 99% Fall For #Shorts",
                "Why Your Brain Makes You Forgive the Wrong Person #Shorts"
            ],
            "hook_quote": "Notice how their tone shifts right here? That's textbook manipulation...",
            "hook_options": [
                {"type": "Warning",   "hook": "If someone ever does this to you, walk away immediately!"},
                {"type": "Exposure",  "hook": "99% of people fail to realize they are being psychologically conditioned..."},
                {"type": "Curiosity", "hook": "Why do our minds naturally defend the people who secretly harm us?"}
            ],
            "summary": "Deep breakdown of psychological manipulation and covert body language signs.",
            "viral_reason": "Self-defense awareness and dark psychology secrets trigger high bookmarking and shares.",
            "commentary": "Practical behavioral psychology framework to detect and neutralize covert control.",
            "subtitle_highlights": ["WARNING", "MANIPULATION", "TRUTH", "POWER"],
            "broll_cues": ["close-up suspicious eye contact", "body language micro-expression analysis", "dark psychology concept visualization"],
            "sfx_cues": ["0s: tense piano note", "3s: revelation ding", "20s: psychological thriller beat", "End: warning alarm"],
            "tiktok_hashtags": "#psychology #darkpsychology #manipulation #mindset #selfprotect #shorts #fyp",
            "fb_cta": "Have you ever encountered this toxic pattern in real life? Share your thoughts below! 👇",
        },
        "deep_sea_earth_mysteries": {
            "moment_types": ["🌊 UNSOLVED ABYSS", "🔬 SCIENTIFIC ANOMALY", "🚫 CLASSIFIED PHENOMENON"],
            "fallback_titles": [
                "The Classified Signal Recorded 11,000m Below Sea Level #Shorts",
                "Science Still Cannot Explain What Appeared Here #Shorts",
                "The Mariana Trench Anomaly Nobody Dares to Talk About #Shorts"
            ],
            "hook_quote": "Deep in the abyss, sonar detected something that defied physics...",
            "hook_options": [
                {"type": "Mystery",    "hook": "Even after 30 years of advanced oceanography, science remains baffled by this..."},
                {"type": "Classified", "hook": "Declassified acoustic recordings reveal an impossible underwater phenomenon!"},
                {"type": "Curiosity",  "hook": "What is lurking in the absolute depths where sunlight can never reach?"}
            ],
            "summary": "Mysterious oceanic anomaly recorded at extreme depths that defies explanation.",
            "viral_reason": "Thalassophobia and cosmic-scale curiosity gap creates irresistible urge to watch until the end.",
            "commentary": "Scientific analysis of competing hypotheses and acoustic data.",
            "subtitle_highlights": ["CLASSIFIED", "UNEXPLAINED", "DEEP", "UNKNOWN"],
            "broll_cues": ["deep ocean bioluminescent creature", "dark mariana trench drone footage", "classified sonar anomaly waveform"],
            "sfx_cues": ["0s: deep ocean ambient hum", "3s: eerie sonar ping", "20s: cosmic mystery build", "End: unsolved mystery fade"],
            "tiktok_hashtags": "#ocean #mystery #deepsea #classified #unexplained #science #shorts #fyp",
            "fb_cta": "What do you believe created this anomaly? Drop your theories below! 🌊🔬",
        },
    }


    def download_segment_or_full(
        self,
        url_or_id: str,
        start_seconds: Optional[float] = None,
        end_seconds: Optional[float] = None,
    ) -> str:
        """Tải video YouTube ở độ phân giải 720p/1080p bằng yt-dlp vào thư mục tạm (thread-safe)."""
        video_id = extract_youtube_video_id(url_or_id) or "source_video"
        target_path = DOWNLOADS_DIR / f"{video_id}.mp4"

        # Sử dụng lock theo video_id để tránh việc 2 máy cùng tải đè 1 file
        lock = _get_video_download_lock(video_id)
        with lock:
            if target_path.exists() and target_path.stat().st_size > 1024 * 1024:
                logger.info(f"Đã có sẵn file tải trước đó: {target_path}")
                return str(target_path)

            from app.services.media.bilibili_fetcher import (
                download_full_bilibili_video,
                is_bilibili_source,
            )
            if is_bilibili_source(url_or_id) or is_bilibili_source(video_id):
                logger.info(f"🐉 [Repurposer] Đang tải video 1080p từ Bilibili (WBI Direct Stream): {url_or_id}")
                b_res = download_full_bilibili_video(url_or_id, str(target_path))
                if b_res and target_path.exists() and target_path.stat().st_size > 100 * 1024:
                    logger.info(f"✅ [Repurposer] Tải thành công video Bilibili: {target_path} ({target_path.stat().st_size} bytes)")
                    return str(target_path)
                raise RuntimeError(f"Không thể tải video từ Bilibili ({url_or_id}).")

            import yt_dlp
            watch_url = f"https://www.youtube.com/watch?v={video_id}" if len(video_id) == 11 else url_or_id
            logger.info(f"Đang tải video từ YouTube (Bypass Bot-Check): {watch_url}")

            # Chiến lược tải đa tầng (Multi-tier Download Strategy chống chặn Bot)
            download_configs = [
                # Tier 1: Ưu tiên chất lượng cao nhất 1080p với client ios + android
                {
                    "client_combo": "ios_android",
                    "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                },
                # Tier 2: Thử android-first với format tương thích cao
                {
                    "client_combo": "android_first",
                    "format": "best[ext=mp4]/best",
                },
                # Tier 3: Client mweb hoặc fallback stream MP4 bất kỳ
                {
                    "client_combo": "mweb_first",
                    "format": "18/best",
                },
            ]

            download_success = False
            last_download_err = None

            for cfg in download_configs:
                try:
                    ydl_opts = self._build_yt_dlp_opts(
                        base_opts={
                            "format": cfg["format"],
                            "outtmpl": str(target_path),
                            "merge_output_format": "mp4",
                        },
                        client_combo=cfg["client_combo"]
                    )
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([watch_url])

                    if target_path.exists() and target_path.stat().st_size > 100 * 1024:
                        download_success = True
                        logger.info(f"✅ Tải video thành công bằng cấu hình {cfg['client_combo']} (kích thước: {target_path.stat().st_size} bytes)")
                        break
                except Exception as dl_err:
                    last_download_err = dl_err
                    logger.warning(f"Thử tải bằng {cfg['client_combo']} không thành công: {dl_err}. Đang chuyển cấu hình dự phòng...")

            if not download_success or not target_path.exists():
                raise RuntimeError(
                    f"Không thể tải video từ YouTube ({video_id}). "
                    f"YouTube yêu cầu xác thực hoặc giới hạn IP. Lỗi chi tiết: {last_download_err}"
                )

            return str(target_path)

    def list_local_videos(self) -> List[Dict[str, Any]]:
        """Quét các video MP4 có sẵn trong thư mục output/ để người dùng chọn tái chế tức thì."""
        results = []
        scan_dirs = [
            Path("output"),
            Path("output/repurposer/downloads"),
        ]
        seen_paths = set()

        for s_dir in scan_dirs:
            if not s_dir.exists():
                continue
            for f in s_dir.glob("*.mp4"):
                if f.name.startswith("short_") or f.name.startswith("temp_"):
                    continue
                p_str = str(f.resolve())
                if p_str in seen_paths:
                    continue
                seen_paths.add(p_str)

                try:
                    size_mb = round(f.stat().st_size / (1024 * 1024), 2)
                    dur_s = get_duration_ms(p_str) / 1000.0
                    results.append({
                        "filename": f.name,
                        "file_path": p_str,
                        "file_size_mb": size_mb,
                        "duration_seconds": round(dur_s, 1),
                        "modified_at": f.stat().st_mtime,
                        "title": f.stem.replace("_", " ").title(),
                    })
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("modified_at", 0), reverse=True)
        return results
