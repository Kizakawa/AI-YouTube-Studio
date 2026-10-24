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


class VideoRepurposerService:
    """Dịch vụ trích xuất và sản xuất Shorts viral từ video dài."""

    def __init__(self):
        self.content_agent = ContentAgent()

    # ── 1. LẤY THÔNG TIN & TRANSCRIPT VIDEO ────────────────────────────────────

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
                {"type": "Stats",    "hook": "Over 1,500 PSI of pure crushing force — nothing escapes!"},
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

    # ── 2B. AI MASHUP STORYBOARD (CẮT GHÉP ĐA PHÂN ĐOẠN THEO NHỊP ĐIỆU VIRAL) ──

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

    # ── 3. TẢI VIDEO YOUTUBE BẰNG YT-DLP ──────────────────────────────────────

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

    def _synthesize_voiceover_sync(
        self,
        text: str,
        output_path: str,
        niche_id: str = "general",
        voice_override: Optional[str] = None,
    ) -> bool:
        """
        Sinh file audio giọng đọc AI đồng bộ (VoiceStudio-standard) từ văn bản sử dụng Edge-TTS.
        Đặc biệt:
        - Tự động nhận diện ngôn ngữ: Văn bản tiếng Việt dùng vi-VN-NamMinhNeural / vi-VN-HoaiMyNeural;
          Văn bản tiếng Anh (US Export) dùng US Neural Voice Persona tương ứng với Niche.
        - Cơ chế retry và voice fallback dự phòng chống rớt mạng.
        - Khả năng chạy an toàn đa luồng/event loop (tránh asyncio.run runtime crash).
        """
        import asyncio
        import concurrent.futures
        import edge_tts
        from app.agents.voice_agent import VoiceAgent

        clean_text = SubtitleHelper.sanitize_subtitle_text(text)
        if not clean_text or len(clean_text.strip()) < 2:
            return False

        # Xác định giọng đọc phù hợp
        # 🇺🇸 US Export Mode: Nếu đang bật chế độ xuất khẩu Hoa Kỳ (DISABLE_VIETNAMESE_VOICE),
        # tự động chuyển dịch toàn bộ văn bản sang tiếng Anh và chỉ dùng US Neural Voice Personas
        if VoiceAgent.DISABLE_VIETNAMESE_VOICE:
            if VoiceAgent._is_vietnamese(clean_text):
                clean_text = self._ensure_english_narration(clean_text, niche_id)
            is_vn = False
            if voice_override and not voice_override.lower().startswith("vi-"):
                primary_voice = voice_override
            else:
                primary_voice = VoiceAgent.get_persona_voice(niche_id=niche_id)
            candidate_voices = [primary_voice]
            for v in ["en-US-AndrewNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-JennyNeural"]:
                if v not in candidate_voices:
                    candidate_voices.append(v)
            tts_rate = "+12%"
        else:
            is_vn = VoiceAgent._is_vietnamese(clean_text)
            if voice_override:
                primary_voice = voice_override
                if voice_override.lower().startswith("vi-"):
                    is_vn = True
                elif voice_override.lower().startswith("en-"):
                    is_vn = False
            elif is_vn:
                primary_voice = "vi-VN-NamMinhNeural"
            else:
                primary_voice = VoiceAgent.get_persona_voice(niche_id=niche_id)

            candidate_voices = [primary_voice]
            if is_vn:
                for v in ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]:
                    if v not in candidate_voices:
                        candidate_voices.append(v)
            else:
                for v in ["en-US-AndrewNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-JennyNeural"]:
                    if v not in candidate_voices:
                        candidate_voices.append(v)
            tts_rate = "+12%" if not is_vn else "+7%"

        async def _do_gen():
            for v in candidate_voices:
                for attempt in range(2):
                    try:
                        comm = edge_tts.Communicate(clean_text, v, rate=tts_rate)
                        await comm.save(output_path)
                        p = Path(output_path)
                        if p.exists() and p.stat().st_size > 800:
                            return True
                    except Exception as err:
                        logger.warning(f"[Voiceover] Lỗi Edge-TTS '{v}' (attempt {attempt+1}): {err}")
                        await asyncio.sleep(0.5)
            return False

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    success = pool.submit(asyncio.run, _do_gen()).result(timeout=25)
            else:
                success = asyncio.run(_do_gen())
            return bool(success and Path(output_path).exists() and Path(output_path).stat().st_size > 800)
        except Exception as e:
            logger.warning(f"[Voiceover] Lỗi tạo thuyết minh AI cho '{clean_text[:40]}...': {e}")
            return False

    # ── 4. CẮT VIDEO, VERTICAL 9:16 CROP & GHÉP PHỤ ĐỀ KARAOKE ─────────────────

    def render_repurposed_short(
        self,
        source_video_path: str,
        start_seconds: float,
        end_seconds: float,
        output_filename: str,
        niche_id: str = "general",
        layout_mode: str = "smart_crop_full",
        audio_mode: str = "original",
        add_subtitles: bool = True,
        subtitle_text: str = "",
        timed_transcript: Optional[List[Dict[str, Any]]] = None,
        credit_text: Optional[str] = None,
        device_id: str = "default_device",
        device_name: str = "Thiết bị khách",
        client_ip: str = "",
        title: str = "",
        description: str = "",
        channel_name: str = "",
        source_url: str = "",
        author: str = "",
        mode: str = "self_owned",
        crop_focus: str = "center",
        mute_original: bool = False,
        voice_id: Optional[str] = None,
        custom_voiceover_text: Optional[str] = None,
        bgm_mood: Optional[str] = None,
        blur_watermark: bool = False,
        watermark_position: str = "top_right",
    ) -> Dict[str, Any]:
        """
        Cắt đoạn highlight, chuyển sang tỉ lệ dọc 9:16 (1080x1920) hoặc vuông 1:1, bóc tách sandwich banners,
        làm mờ logo watermark và đốt phụ đề Karaoke ASS chuẩn Niche Profile.
        Tích hợp toàn diện:
        1. VAD Dead-Air Removal: Tự động dịch mốc cắt tới điểm bắt đầu giọng nói, triệt tiêu khoảng lặng ở 2s đầu.
        2. Framing thông minh: smart_crop_full, strip_sandwich_square (bóc tách 1:1), strip_sandwich_clean_916, strip_sandwich_bokeh.
        3. Phụ đề Karaoke Dynamic: Kết nối trực tiếp mốc thời gian thực từng từ từ timed_transcript hoặc sinh theo giọng đọc mới.
        4. Audio Envelope & Vocal Ducking: Bắt buộc áp dụng fade-in/out 0.25s; hỗ trợ Tắt tiếng cũ + Lồng giọng đọc AI 100% US English.
        5. Pre-Flight QA Gate: Tự động kiểm tra LUFS, dead-air và độ đồng bộ trước khi bàn giao clip.
        """
        if not os.path.exists(source_video_path):
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {source_video_path}")

        from app.agents.voice_agent import VoiceAgent

        # Nhận diện chế độ âm thanh
        is_voiceover_mode = audio_mode in (
            "mute_original_voiceover",
            "ai_voiceover_mute",
            "ai_voiceover",
            "duck_original_voiceover",
        )
        should_mute_original = (
            audio_mode in ("mute_original_voiceover", "ai_voiceover_mute", "mute")
            or (audio_mode == "ai_voiceover" and mute_original)
        )

        # 0. Tích hợp Voice Activity Detection (VAD) để loại bỏ dead-air ở đầu video
        speech_offset = detect_audio_speech_start(
            file_path=source_video_path,
            start_seconds=start_seconds,
            scan_duration=2.5,
            noise_threshold_db=-32.0,
        )
        if speech_offset > 0.15 and (end_seconds - (start_seconds + speech_offset)) >= 8.0:
            logger.info(f"VAD: Tự động dịch start_seconds từ {start_seconds:.2f}s lên {start_seconds + speech_offset:.2f}s (cắt bỏ {speech_offset:.2f}s dead-air)")
            start_seconds += speech_offset

        duration = max(end_seconds - start_seconds, 5.0)
        output_path = CLIPS_DIR / output_filename
        output_path_str = str(output_path)
        temp_files: List[Path] = []

        # Chuẩn hóa tiêu đề tiếng Anh viral
        clean_title = self._ensure_viral_english_title(title, niche_id)

        # Xác định văn bản giọng đọc thuyết minh (dành cho chế độ lồng tiếng mới)
        raw_voice_text = custom_voiceover_text or subtitle_text or title or ""
        # 🇺🇸 US Export Mode: Tự động chuyển dịch sang tiếng Anh nếu phát hiện tiếng Việt
        if VoiceAgent.DISABLE_VIETNAMESE_VOICE and VoiceAgent._is_vietnamese(raw_voice_text):
            clean_voice_text = self._ensure_english_narration(raw_voice_text, niche_id)
        else:
            clean_voice_text = SubtitleHelper.sanitize_subtitle_text(raw_voice_text)

        if not clean_voice_text or len(clean_voice_text.split()) < 2:
            clean_voice_text = self._ensure_english_narration("", niche_id)

        # Tự động tạo mô tả video & bình luận ghim kèm link Store ID kizakawa-20 (Amazon Associates)
        from app.services.monetization_engine import (
            build_repurposed_video_description,
            build_repurposed_pinned_comment,
            find_best_amazon_product,
            build_amazon_url
        )
        if not description or not description.strip():
            description = build_repurposed_video_description(
                title=clean_title,
                summary=subtitle_text,
                niche_id=niche_id,
                channel_name=channel_name,
                source_url=source_url,
                author=author,
                mode=mode,
                tracking_tag="kizakawa-20",
            )
        amazon_prod = find_best_amazon_product(niche=niche_id, title=clean_title)
        amazon_prod["affiliate_url"] = build_amazon_url(
            amazon_prod["asin"],
            "kizakawa-20",
            is_search=amazon_prod.get("is_search", False)
        )

        # Tự động tạo bình luận ghim kèm Store ID kizakawa-20
        pinned_comment = build_repurposed_pinned_comment(
            title=clean_title,
            niche_id=niche_id,
            source_url=source_url,
            author=author,
            channel_name=channel_name,
            tracking_tag="kizakawa-20",
        )

        # Lấy thông tin phụ đề Niche Profile
        niche_profile = get_niche(niche_id)
        niche_sub = get_niche_subtitle_style(niche_profile)
        fontname = niche_sub.get("fontname", "Arial Black")
        highlight_color = niche_sub.get("secondary_ass", "&H0000FFFF")

        # 1. Tạo file phụ đề ASS nếu có yêu cầu
        ass_file_path = None
        effective_credit = credit_text
        if not effective_credit and author:
            effective_credit = f"Via: {author} • Full Video in Desc"
        elif not effective_credit and source_url:
            effective_credit = "Full Video in Description"

        if add_subtitles:
            ass_filename = f"sub_{Path(output_filename).stem}.ass"
            ass_path = SUBS_DIR / ass_filename
            ass_file_path = str(ass_path)

            eff_margin_v = 200 if layout_mode == "strip_sandwich_square" else 580

            if is_voiceover_mode:
                # Khi lồng giọng đọc AI/Tự sinh: Phụ đề BẮT BUỘC phải khớp 100% với lời thuyết minh mới,
                # KHÔNG dùng timed_transcript của video cũ (vì từ ngữ và thời điểm phát âm đã thay đổi hoàn toàn).
                from app.schemas.contracts import SceneContract
                logger.info(f"Tạo phụ đề Kinetic Karaoke ASS khớp giọng đọc mới: '{clean_voice_text[:40]}...'")
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_voice_text,
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                )
            elif timed_transcript and len(timed_transcript) > 0:
                # GẮN KẾT TRỰC TIẾP TIMED TRANSCRIPT (MỐC THỜI GIAN THẬT TỪNG TỪ)
                logger.info(f"Tạo phụ đề Karaoke ASS từ {len(timed_transcript)} timed segments chuẩn xác.")
                SubtitleHelper.save_ass_from_timed_segments(
                    timed_segments=timed_transcript,
                    output_path=ass_file_path,
                    total_duration_sec=duration,
                    clip_start_offset=start_seconds,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                    show_cta_badge=True,
                )
            elif subtitle_text.strip():
                clean_sub = SubtitleHelper.sanitize_subtitle_text(subtitle_text)
                from app.schemas.contracts import SceneContract
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_sub,
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=42,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                    show_cta_badge=True,
                )
            else:
                # Nếu không có transcript, sinh phụ đề hook dẫn chuyện theo tiêu đề video
                from app.schemas.contracts import SceneContract
                clean_title_sub = SubtitleHelper.sanitize_subtitle_text(clean_title)
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_title_sub or "WATCH THIS UNBELIEVABLE MOMENT!",
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                )

        # Kiểm tra xem video nguồn có audio stream không
        has_audio = False
        # Probe audio & video stream
        has_audio = False
        is_source_vertical = True  # Mặc định ưu tiên layout dọc chuẩn video bánh kẹp TikTok/Shorts
        try:
            info = probe_file(source_video_path)
            if info and "streams" in info:
                has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
                for s in info["streams"]:
                    if s.get("codec_type") == "video":
                        vw = int(s.get("width", 0))
                        vh = int(s.get("height", 0))
                        if vw > 0 and vh > 0:
                            is_source_vertical = (vh >= vw)
                        break
        except Exception as e:
            logger.warning(f"Lỗi probe source stream: {e}")
            has_audio = True

        sandwich_core = "crop=in_w:in_h*0.51:0:in_h*0.205" if is_source_vertical else "crop=in_h:in_h:(in_w-in_h)/2:0"

        # Chuẩn hóa crop_focus: 'center' (mặc định), 'left' (khách mời/khung trái), 'right' (MC/khung phải)
        focus = (crop_focus or "center").strip().lower()
        if focus not in ("left", "right", "center"):
            focus = "center"

        if focus == "left":
            smart_crop_x = "(in_w-out_w)*0.18"
            center_crop_x = "(iw-ih*9/16)*0.18"
        elif focus == "right":
            smart_crop_x = "(in_w-out_w)*0.82"
            center_crop_x = "(iw-ih*9/16)*0.82"
        else: # center
            smart_crop_x = "(in_w-out_w)/2"
            center_crop_x = "(iw-ih*9/16)/2"

        # 2. Xây dựng bộ lọc FFmpeg chuyển sang tỉ lệ dọc 9:16 (1080x1920) hoặc vuông 1:1, bóc tách sandwich & làm mờ logo
        watermark_pos = (watermark_position or "top_right").lower()
        if watermark_pos == "top_left":
            wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.02", "iw*0.38", "ih*0.11"
            wm_ov_x, wm_ov_y = "W*0.02", "H*0.02"
        elif watermark_pos == "bottom_right":
            wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.87", "iw*0.38", "ih*0.11"
            wm_ov_x, wm_ov_y = "W*0.60", "H*0.87"
        elif watermark_pos == "bottom_left":
            wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.87", "iw*0.38", "ih*0.11"
            wm_ov_x, wm_ov_y = "W*0.02", "H*0.87"
        else: # top_right
            wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.02", "iw*0.38", "ih*0.11"
            wm_ov_x, wm_ov_y = "W*0.60", "H*0.02"

        def _apply_watermark_and_sub(source_pad: str, dest_pad: str = "v") -> str:
            segments = []
            cur = source_pad
            if blur_watermark:
                segments.append(
                    f"{cur}split[wm_in][wm_crp];"
                    f"[wm_crp]crop={wm_crop_w}:{wm_crop_h}:{wm_crop_x}:{wm_crop_y},boxblur=18:5[wm_blured];"
                    f"[wm_in][wm_blured]overlay={wm_ov_x}:{wm_ov_y}[v_wm_done]"
                )
                cur = "[v_wm_done]"

            if ass_file_path and os.path.exists(ass_file_path):
                escaped_ass = ass_file_path.replace("\\", "/").replace(":", "\\:")
                segments.append(f"{cur}ass='{escaped_ass}'[{dest_pad}]")
            else:
                segments.append(f"{cur}null[{dest_pad}]")
            return ";".join(segments)

        filter_parts = []
        if layout_mode == "strip_sandwich_square":
            # ⏹️ Bóc tách video vuông 1:1 nguyên bản (Cắt bỏ 100% banner trên và footer phụ đề dưới, xuất 1080x1080)
            v_core = (
                f"[0:v]{sandwich_core},"
                "scale=1080:1080,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_sq]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_sq]", "v"))

        elif layout_mode == "strip_sandwich_clean_916":
            # ✂️ Bóc tách lõi giữa và phóng to lấp đầy 9:16 Shorts (1080x1920) không bị dính chữ của banner/phụ đề cũ
            v_core = (
                f"[0:v]{sandwich_core},"
                "scale=1080*1.78:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_916]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_916]", "v"))

        elif layout_mode == "strip_sandwich_bokeh":
            # 🎨 Bóc tách lõi giữa đặt trên nền Bokeh Blur 9:16 sạch đẹp (1080x1920), triệt tiêu hoàn toàn viền chữ cũ
            v_core = (
                f"[0:v]{sandwich_core}[c_raw];"
                "[c_raw]split[c_bg][c_fg];"
                "[c_bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=15:3,scale=1080:1920[bg];"
                "[c_fg]scale=1080:1080,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                "[bg][fg]overlay=0:(H-h)/2[v_core_bk]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_bk]", "v"))

        elif layout_mode == "smart_crop_full":
            v_smart = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_sm]"
            )
            filter_parts.append(v_smart)
            filter_parts.append(_apply_watermark_and_sub("[v_core_sm]", "v"))

        elif layout_mode == "center_crop":
            v_crop = (
                f"[0:v]crop=ih*9/16:ih:{center_crop_x}:0,scale=1080:1920,"
                "eq=contrast=1.03:brightness=0.01:saturation=1.04[v_core_cc]"
            )
            filter_parts.append(v_crop)
            filter_parts.append(_apply_watermark_and_sub("[v_core_cc]", "v"))

        else: # blur_background
            v_blur = (
                "[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,scale=1080:1920[bg];"
                "[0:v]crop=iw*0.92:ih*0.90:(iw-out_w)/2:(ih-out_h)/2,scale=1080:-2:flags=lanczos,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v_core_bb]"
            )
            filter_parts.append(v_blur)
            filter_parts.append(_apply_watermark_and_sub("[v_core_bb]", "v"))

        # Bộ lọc audio: Envelope Fade In/Out (0.25s) bắt buộc chống clipping/popping
        fade_out_st = max(0.0, duration - 0.3)
        extra_input_args = []
        voice_tmp_path = None

        if is_voiceover_mode:
            # Thuyết minh AI mới chuẩn VoiceStudio:
            # 1. 🎙️ Voice Persona Registry: Hỗ trợ cả giọng Việt và giọng Mỹ đa dạng theo lựa chọn
            # 2. ⏱️ Timed Speech Synchronization: Tự động co/giãn tốc độ qua atempo để khớp khít thời lượng video clip
            # 3. 🔇 Tắt hoàn toàn âm thanh video cũ (should_mute_original) hoặc hạ nhỏ âm lượng (Ducking)
            try:
                voice_tmp_file = REPURPOSER_DIR / f"temp_voice_{Path(output_filename).stem}.mp3"
                voice_tmp_path = str(voice_tmp_file)
                temp_files.append(voice_tmp_file)

                voice_ok = self._synthesize_voiceover_sync(
                    text=clean_voice_text,
                    output_path=voice_tmp_path,
                    niche_id=niche_id,
                    voice_override=voice_id,
                )

                if voice_ok and os.path.exists(voice_tmp_path) and os.path.getsize(voice_tmp_path) > 800:
                    extra_input_args = ["-i", voice_tmp_path]

                    # Tính toán tỷ lệ đồng bộ thời gian (Timed Speech Sync: Giữ nguyên 1.0x tự nhiên, không bao giờ kéo lê làm chậm giọng)
                    voice_dur_ms = get_duration_ms(voice_tmp_path)
                    voice_dur_s = (voice_dur_ms / 1000.0) if voice_dur_ms > 0 else duration
                    if voice_dur_s <= duration:
                        tempo_str = "anull"
                    else:
                        speed_ratio = min(voice_dur_s / max(duration, 1.0), 1.25)
                        tempo_str = build_atempo_filter(speed_ratio)
                    tempo_chain = f"{tempo_str}," if tempo_str != "anull" else ""

                    if should_mute_original:
                        # 🔇 TẮT HOÀN TOÀN ÂM THANH GỐC: [0:a] bị triệt tiêu 100%!
                        bgm_target = None
                        if bgm_mood and bgm_mood not in ("none", "mute"):
                            bgm_map = {
                                "suspense": "assets/bgm/dark_suspense.wav",
                                "epic": "assets/bgm/inspiring_epic.wav",
                                "chill": "assets/bgm/chill_ambient.mp3",
                                "cyber": "assets/bgm/scifi_cyber.wav",
                            }
                            cand_target = bgm_map.get(bgm_mood)
                            if cand_target and os.path.exists(cand_target):
                                bgm_target = cand_target

                        if bgm_target:
                            extra_input_args += ["-stream_loop", "-1", "-i", bgm_target]
                            # Input 0: video, Input 1: voiceover mp3, Input 2: bgm audio
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f}[voice];"
                                f"[2:a]lowpass=f=4200,equalizer=f=3200:t=q:w=1.2:g=-6,volume=0.035,aresample=44100,aformat=channel_layouts=stereo,atrim=0:{duration:.3f}[bgm];"
                                f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        else:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        has_audio = True
                    else:
                        # Chế độ Ducking: Giữ âm thanh gốc nhưng ép nhỏ khi có giọng nói
                        if has_audio:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.15,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},asplit=2[v_sc][v_mix];"
                                f"[0:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{duration:.3f}[bg_clean];"
                                f"[bg_clean][v_sc]sidechaincompress=threshold=0.03:ratio=7:attack=20:release=350[bg_ducked];"
                                f"[bg_ducked][v_mix]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        else:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.15,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        has_audio = True
            except Exception as e:
                logger.warning(f"Lỗi tạo AI Voiceover: {e}, fallback sang original audio")

        elif audio_mode == "mute":
            has_audio = False

        if not extra_input_args and has_audio and audio_mode != "mute":
            # Chế độ Original: Anti-Content ID (micro pitch shift 1.5%) + Envelope Fade
            filter_parts.append(
                f"[0:a]asetrate=44100*1.015,atempo=1/1.015,aresample=44100,"
                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
            )

        filter_complex_str = ";".join(filter_parts)

        # 3. Chạy FFmpeg cắt chính xác và render (có Semaphore giới hạn tải đồng thời)
        ffmpeg_args = [
            "-ss", str(start_seconds),
            "-t", str(duration),
            "-i", source_video_path,
        ]
        if extra_input_args:
            ffmpeg_args += extra_input_args

        ffmpeg_args += [
            "-filter_complex", filter_complex_str,
            "-map", "[v]",
        ]
        if has_audio:
            ffmpeg_args += ["-map", "[a]"]
        else:
            ffmpeg_args += ["-an"]

        ffmpeg_args += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            *BT709_COLOR_ARGS,
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            output_path_str,
        ]

        logger.info(f"[{device_name} ({client_ip})] Đang render Short 9:16 ({layout_mode}, {audio_mode}): {output_path_str}")
        with _render_semaphore:
            run_ffmpeg(ffmpeg_args, timeout=360)

        if not os.path.exists(output_path_str):
            raise RuntimeError(f"Render Short thất bại, không tìm thấy file: {output_path_str}")

        file_size_mb = round(os.path.getsize(output_path_str) / (1024 * 1024), 2)

        # Lưu file metadata định danh thiết bị tạo clip và thông tin Amazon
        meta_file = CLIPS_DIR / f"{Path(output_filename).stem}.meta.json"
        meta_data = {
            "filename": output_filename,
            "device_id": device_id,
            "device_name": device_name,
            "client_ip": client_ip,
            "created_at": time.time(),
            "duration": round(duration, 1),
            "file_size_mb": file_size_mb,
            "niche_id": niche_id,
            "layout_mode": layout_mode,
            "audio_mode": audio_mode,
            "mute_original": should_mute_original,
            "voice_id": voice_id or "",
            "voiceover_text": clean_voice_text if is_voiceover_mode else "",
            "title": clean_title,
            "subtitle_text": subtitle_text[:120],
            "description": description,
            "pinned_comment": pinned_comment,
            "amazon_product": amazon_prod,
            "source_url": source_url,
            "author": author,
            "mode": mode,
        }

        # 4. Kiểm định chất lượng Pre-Flight QA Gate tự động trước khi bàn giao
        qa_report = preflight_repurpose_qa(output_path_str, ass_file_path, meta_data)
        meta_data["qa_report"] = qa_report
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(meta_data, mf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Lỗi lưu clip meta: {e}")

        # Xóa voice file tạm và các temp_files nếu có
        for tf in temp_files:
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass
        if voice_tmp_path and os.path.exists(voice_tmp_path):
            try:
                os.remove(voice_tmp_path)
            except Exception:
                pass

        return {
            "success": True,
            "output_path": output_path_str,
            "relative_url": f"/media/repurposer/clips/{output_filename}",
            "filename": output_filename,
            "duration": round(duration, 1),
            "file_size_mb": file_size_mb,
            "niche_id": niche_id,
            "layout_mode": layout_mode,
            "audio_mode": audio_mode,
            "device_id": device_id,
            "device_name": device_name,
            "client_ip": client_ip,
            "title": clean_title,
            "description": description,
            "pinned_comment": pinned_comment,
            "amazon_product": amazon_prod,
            "qa_report": qa_report,
        }

    # ── 4B. RENDER MULTI-CLIP VIRAL MASHUP SHORT (TÁI CHẾ NẤU LẠI ĐA PHÂN ĐOẠN) ──

    def render_mashup_short(
        self,
        source_video_path: str,
        clips: List[Dict[str, Any]],
        output_filename: str,
        niche_id: str = "general",
        layout_mode: str = "smart_crop_full",
        audio_mode: str = "original",
        add_subtitles: bool = True,
        enable_sfx: bool = True,
        bgm_mood: Optional[str] = "suspense",
        title: str = "",
        crop_focus: str = "center",
        device_id: str = "default_device",
        device_name: str = "Thiết bị khách",
        client_ip: str = "",
        source_url: str = "",
        author: str = "",
        mode: str = "self_owned",
        channel_name: str = "",
        description: str = "",
        mute_original: bool = False,
        voice_id: Optional[str] = None,
        blur_watermark: bool = False,
        watermark_position: str = "top_right",
    ) -> Dict[str, Any]:
        """
        Nấu lại video dạng Mashup Multi-Clip:
        1. Trích xuất từng micro-clip với tỉ lệ 9:16 hoặc vuông 1:1, bóc tách sandwich banners.
        2. Tự động làm mờ logo watermark (Anti-Watermark Blur) nếu được yêu cầu.
        3. Drop phụ đề cũ bằng cờ -sn (Lossless-Cut inspired).
        4. Ghép nối liền mạch các clips không bị giật lag khung hình.
        5. Chèn hiệu ứng SFX Whoosh tại điểm nhảy cảnh và nhạc nền BGM ducking xuyên suốt.
        6. Đốt phụ đề Karaoke ASS chuẩn Safe Zone (100% US English).
        """
        import shutil
        if not os.path.exists(source_video_path):
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {source_video_path}")

        from app.agents.voice_agent import VoiceAgent

        if not clips or len(clips) < 2:
            raise ValueError("Chế độ Mashup yêu cầu tối thiểu 2 phân đoạn micro-clips.")

        # Nhận diện chế độ âm thanh
        is_voiceover_mode = audio_mode in (
            "mute_original_voiceover",
            "ai_voiceover_mute",
            "ai_voiceover",
            "duck_original_voiceover",
        )
        should_mute_original = (
            audio_mode in ("mute_original_voiceover", "ai_voiceover_mute", "mute")
            or (audio_mode == "ai_voiceover" and mute_original)
        )

        # Probe audio & video stream
        has_audio = False
        is_source_vertical = True  # Mặc định ưu tiên layout dọc chuẩn video bánh kẹp TikTok/Shorts
        try:
            info = probe_file(source_video_path)
            if info and "streams" in info:
                has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
                for s in info["streams"]:
                    if s.get("codec_type") == "video":
                        vw = int(s.get("width", 0))
                        vh = int(s.get("height", 0))
                        if vw > 0 and vh > 0:
                            is_source_vertical = (vh >= vw)
                        break
        except Exception:
            has_audio = True

        sandwich_core = "crop=in_w:in_h*0.51:0:in_h*0.205" if is_source_vertical else "crop=in_h:in_h:(in_w-in_h)/2:0"

        uid = uuid.uuid4().hex[:8]
        temp_files: List[Path] = []
        output_path = CLIPS_DIR / output_filename
        output_path_str = str(output_path)

        with _render_semaphore:
            try:
                subclip_paths = []
                clean_title = self._ensure_viral_english_title(title, niche_id)

                focus = (crop_focus or "center").strip().lower()
                if focus == "left":
                    smart_crop_x = "(in_w-out_w)*0.18"
                    center_crop_x = "(iw-ih*9/16)*0.18"
                elif focus == "right":
                    smart_crop_x = "(in_w-out_w)*0.78"
                    center_crop_x = "(iw-ih*9/16)*0.78"
                else:
                    smart_crop_x = "(in_w-out_w)/2"
                    center_crop_x = "(iw-ih*9/16)/2"

                watermark_pos = (watermark_position or "top_right").lower()
                if watermark_pos == "top_left":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.02", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.02", "H*0.02"
                elif watermark_pos == "bottom_right":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.87", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.60", "H*0.87"
                elif watermark_pos == "bottom_left":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.87", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.02", "H*0.87"
                else: # top_right
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.02", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.60", "H*0.02"

                def _apply_wm_filter(base_expr: str) -> str:
                    if not blur_watermark:
                        return f"{base_expr}[v]"
                    return (
                        f"{base_expr}[raw_c];"
                        f"[raw_c]split[wm_in][wm_crp];"
                        f"[wm_crp]crop={wm_crop_w}:{wm_crop_h}:{wm_crop_x}:{wm_crop_y},boxblur=18:5[wm_blured];"
                        f"[wm_in][wm_blured]overlay={wm_ov_x}:{wm_ov_y}[v]"
                    )

                # ── BƯỚC 1: RENDER TỪNG MICRO-CLIP 9:16/1:1 VỚI ZOOM PUNCH VÀ DEAD-AIR FADE ──
                for idx, c in enumerate(clips):
                    st = max(0.0, float(c.get("start_seconds", 0.0)))
                    et = float(c.get("end_seconds", st + 4.0))
                    dur = max(0.8, et - st)
                    zoom = max(1.20, float(c.get("zoom_punch", 1.20)))

                    clip_out = REPURPOSER_DIR / f"temp_{uid}_c{idx}.mp4"
                    temp_files.append(clip_out)

                    if layout_mode == "strip_sandwich_square":
                        base_v = (
                            f"[0:v]{sandwich_core},"
                            "scale=1080:1080,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "strip_sandwich_clean_916":
                        base_v = (
                            f"[0:v]{sandwich_core},"
                            f"scale=1080*{zoom:.2f}:1920*{zoom:.2f}:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "strip_sandwich_bokeh":
                        base_v = (
                            f"[0:v]{sandwich_core}[mc_raw];"
                            "[mc_raw]split[mc_bg][mc_fg];"
                            "[mc_bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=15:3,scale=1080:1920[bg];"
                            "[mc_fg]scale=1080:1080,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                            "[bg][fg]overlay=0:(H-h)/2"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "smart_crop_full":
                        base_v = (
                            f"[0:v]scale=1080*{zoom:.2f}:1920*{zoom:.2f}:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "center_crop":
                        base_v = f"[0:v]crop=ih*9/16:ih:{center_crop_x}:0,scale=1080:1920,eq=contrast=1.03:brightness=0.01:saturation=1.04"
                        v_filter = _apply_wm_filter(base_v)
                    else:  # blur_background
                        base_v = (
                            "[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,scale=1080:1920[bg];"
                            f"[0:v]crop=iw*0.92:ih*0.90:(iw-out_w)/2:(ih-out_h)/2,scale=1080*{zoom:.2f}:-2:flags=lanczos,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                        )
                        v_filter = _apply_wm_filter(base_v)

                    fade_st = max(0.0, dur - 0.06)
                    voice_ok = False
                    voice_clip_file = None

                    if is_voiceover_mode:
                        raw_narration = c.get("custom_voiceover_text") or c.get("transcript_snippet") or c.get("summary") or c.get("text") or ""
                        # 🇺🇸 US Export Mode: Nếu phát hiện tiếng Việt, tự động chuyển dịch sang tiếng Anh viral
                        if VoiceAgent.DISABLE_VIETNAMESE_VOICE and VoiceAgent._is_vietnamese(raw_narration):
                            clean_narration = self._ensure_english_narration(raw_narration, niche_id)
                        else:
                            clean_narration = SubtitleHelper.sanitize_subtitle_text(raw_narration)
                        if not clean_narration or len(clean_narration.split()) < 2:
                            role = str(c.get("role", "CLIP")).upper()
                            default_narr_en = {
                                "HOOK": "The most heartbreaking moment nobody saw coming...",
                                "SETUP": "It all began with a desperate call for help...",
                                "TENSION": "Crawling deep beneath the freezing porch...",
                                "CLIMAX": "A helpless puppy shivering in the dark!",
                                "LOOP": "Follow for the next miracle chapter now!",
                            }
                            clean_narration = default_narr_en.get(role, f"Watch this scene #{idx+1}!")

                        voice_clip_file = REPURPOSER_DIR / f"temp_{uid}_voice_c{idx}.mp3"
                        temp_files.append(voice_clip_file)
                        voice_ok = self._synthesize_voiceover_sync(
                            text=clean_narration,
                            output_path=str(voice_clip_file),
                            niche_id=niche_id,
                            voice_override=voice_id,
                        )

                    if voice_ok and voice_clip_file and voice_clip_file.exists() and voice_clip_file.stat().st_size > 800:
                        v_dur_ms = get_duration_ms(str(voice_clip_file))
                        v_dur_s = (v_dur_ms / 1000.0) if v_dur_ms > 0 else dur
                        c["voice_dur_ms"] = v_dur_ms

                        if v_dur_s <= dur:
                            tempo_str = "anull"
                        else:
                            speed_ratio = min(v_dur_s / max(dur, 0.8), 1.25)
                            tempo_str = build_atempo_filter(speed_ratio)
                        tempo_chain = f"{tempo_str}," if tempo_str != "anull" else ""

                        if should_mute_original or not has_audio:
                            # 🔇 Tắt hoàn toàn âm thanh gốc, chỉ lấy âm thanh giọng đọc mới
                            a_filter = (
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,"
                                f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f},"
                                f"afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                            )
                        else:
                            # Chế độ Ducking: Giữ tiếng gốc nhỏ (ambient)
                            a_filter = (
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,"
                                f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[v_voice];"
                                f"[0:a]volume=0.10,aresample=44100,aformat=channel_layouts=stereo,atrim=0:{dur:.3f}[v_amb];"
                                f"[v_voice][v_amb]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                            )

                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-i", str(voice_clip_file),
                            "-sn",
                            "-filter_complex", f"{v_filter};{a_filter}",
                            "-map", "[v]",
                            "-map", "[a]",
                        ]
                    elif audio_mode == "mute":
                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-sn",
                            "-filter_complex", f"{v_filter}",
                            "-map", "[v]",
                            "-an",
                        ]
                    else:
                        a_filter = f"[0:a]afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-sn",
                        ]
                        if has_audio:
                            clip_cmd += [
                                "-filter_complex", f"{v_filter};{a_filter}",
                                "-map", "[v]",
                                "-map", "[a]",
                            ]
                        else:
                            clip_cmd += [
                                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                                "-filter_complex", v_filter,
                                "-map", "[v]",
                                "-map", "1:a",
                            ]

                    clip_cmd += [
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-r", "30",
                        "-pix_fmt", "yuv420p",
                        *BT709_COLOR_ARGS,
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "44100",
                        "-ac", "2",
                        str(clip_out)
                    ]

                    run_ffmpeg(clip_cmd)
                    subclip_paths.append(str(clip_out))

                # ── BƯỚC 2: GHÉP NỐI CÁC CLIPS (CONCAT DEMUXER SIÊU TỐC) ──
                concat_list_file = REPURPOSER_DIR / f"temp_{uid}_list.txt"
                temp_files.append(concat_list_file)
                with open(concat_list_file, "w", encoding="utf-8") as f:
                    for scp in subclip_paths:
                        abs_p = Path(scp).resolve().as_posix().replace("'", "'\\''")
                        f.write(f"file '{abs_p}'\n")

                temp_concat_video = REPURPOSER_DIR / f"temp_{uid}_concat.mp4"
                temp_files.append(temp_concat_video)

                concat_cmd = [
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_list_file),
                    "-c", "copy",
                    str(temp_concat_video)
                ]
                run_ffmpeg(concat_cmd)

                # ── BƯỚC 3: NHẠC NỀN BGM DUCKING & SFX CHUYỂN CẢNH (WHOOSH) ──
                current_stage_video = temp_concat_video
                total_duration = sum(max(0.8, float(c.get("end_seconds", 4.0)) - max(0.0, float(c.get("start_seconds", 0.0)))) for c in clips)

                # Tìm file BGM phù hợp
                bgm_map = {
                    "suspense": "assets/bgm/dark_suspense.wav",
                    "epic": "assets/bgm/inspiring_epic.wav",
                    "chill": "assets/bgm/chill_ambient.mp3",
                    "cyber": "assets/bgm/scifi_cyber.wav",
                }
                bgm_target = bgm_map.get(bgm_mood or "suspense")
                if not bgm_target or not os.path.exists(bgm_target):
                    for cand in bgm_map.values():
                        if os.path.exists(cand):
                            bgm_target = cand
                            break

                temp_audio_video = REPURPOSER_DIR / f"temp_{uid}_bgm.mp4"
                if bgm_target and os.path.exists(bgm_target) and audio_mode != "mute":
                    temp_files.append(temp_audio_video)
                    bgm_vol = 0.035 if is_voiceover_mode else 0.05
                    # Bộ lọc Cinematic De-Harshing:
                    # 1. lowpass=f=4200: Cắt bỏ tần số cao sắc nhọn (>4.2kHz) gây chói tai và xé tiếng trên loa điện thoại
                    # 2. equalizer=f=3200: Giảm nhẹ dải 3.2kHz tạo cảm giác âm thanh ấm áp, êm dịu, chìm dưới giọng đọc
                    bgm_filter = f"[1:a]lowpass=f=4200,equalizer=f=3200:t=q:w=1.2:g=-6,volume={bgm_vol:.3f}[bgm]"
                    bgm_cmd = [
                        "-y",
                        "-i", str(current_stage_video),
                        "-stream_loop", "-1",
                        "-i", bgm_target,
                        "-filter_complex",
                        f"[0:a]volume=1.0[voice];{bgm_filter};[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                        "-map", "0:v",
                        "-map", "[aout]",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-t", f"{total_duration:.2f}",
                        str(temp_audio_video)
                    ]
                    try:
                        run_ffmpeg(bgm_cmd)
                        current_stage_video = temp_audio_video
                    except Exception as e_bgm:
                        logger.warning(f"[Mashup] Không thể mix BGM: {e_bgm}")

                # SFX chuyển cảnh (Whoosh Transitions & Impact Hit)
                if enable_sfx:
                    temp_sfx_video = REPURPOSER_DIR / f"temp_{uid}_sfx.mp4"
                    temp_files.append(temp_sfx_video)
                    try:
                        from app.utils.ffmpeg_helper import mix_sfx_transitions
                        scene_ts = [0.0]
                        cur = 0.0
                        for c in clips[:-1]:
                            cur += max(0.8, float(c.get("end_seconds", 4.0)) - max(0.0, float(c.get("start_seconds", 0.0))))
                            scene_ts.append(round(cur, 2))

                        mix_sfx_transitions(
                            video_path=str(current_stage_video),
                            output_path=str(temp_sfx_video),
                            scene_timestamps_seconds=scene_ts,
                            sfx_dir="assets/sfx",
                            default_sfx="whoosh",
                            sfx_volume=0.16,
                            hit_sfx="hit",
                            hit_volume=0.18,
                        )
                        if temp_sfx_video.exists() and temp_sfx_video.stat().st_size > 1000:
                            current_stage_video = temp_sfx_video
                    except Exception as e_sfx:
                        logger.warning(f"[Mashup] Không thể mix SFX transitions: {e_sfx}")

                # ── BƯỚC 4: ĐỐT PHỤ ĐỀ KINETIC KARAOKE ASS & NHÃN BỐI CẢNH TIMELINE ──
                ass_path = None
                effective_add_subtitles = add_subtitles if add_subtitles is not None else True
                if effective_add_subtitles:
                    ass_path = SUBS_DIR / f"sub_mashup_{Path(output_filename).stem}.ass"
                    temp_files.append(ass_path)

                    from app.services.niche_manager import get_niche, get_niche_subtitle_style
                    niche_profile = get_niche(niche_id)
                    niche_sub = get_niche_subtitle_style(niche_profile)
                    fontname = niche_sub.get("fontname", "Arial Black")

                    mashup_margin_v = 200 if layout_mode == "strip_sandwich_square" else 580
                    SubtitleHelper.save_mashup_ass_file(
                        clips=clips,
                        output_path=str(ass_path),
                        fontname=fontname,
                        fontsize=44,
                        margin_v=mashup_margin_v,
                        niche_id=niche_id,
                        credit_text=author or channel_name or None,
                        is_english=True,
                        is_voiceover=is_voiceover_mode,
                    )

                    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
                    burn_cmd = [
                        "-y",
                        "-i", str(current_stage_video),
                        "-vf", f"ass='{escaped_ass}'",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-c:a", "copy",
                        str(output_path)
                    ]
                    try:
                        run_ffmpeg(burn_cmd)
                    except Exception as e_sub:
                        logger.warning(f"[Mashup] Lỗi burn phụ đề ASS, fallback dùng video không sub: {e_sub}")
                        shutil.copy2(str(current_stage_video), str(output_path))
                else:
                    shutil.copy2(str(current_stage_video), str(output_path))

                # ── BƯỚC 5: TẠO METADATA MONETIZATION & PRE-FLIGHT QA ──
                from app.services.monetization_engine import (
                    build_repurposed_video_description,
                    build_repurposed_pinned_comment,
                    find_best_amazon_product,
                )
                full_summary = " ".join(c.get("summary", "") for c in clips)
                if not description or not description.strip():
                    description = build_repurposed_video_description(
                        title=clean_title,
                        summary=full_summary,
                        niche_id=niche_id,
                        channel_name=channel_name,
                        source_url=source_url,
                        author=author,
                        mode=mode,
                        tracking_tag="kizakawa-20",
                    )

                pinned_comment = build_repurposed_pinned_comment(
                    title=clean_title,
                    niche_id=niche_id,
                    source_url=source_url,
                    channel_name=channel_name,
                    tracking_tag="kizakawa-20",
                )
                amazon_prod = find_best_amazon_product(clean_title, niche_id)

                file_size_mb = round(output_path.stat().st_size / (1024 * 1024), 2)
                
                meta_file = CLIPS_DIR / f"{Path(output_filename).stem}.meta.json"
                meta_data = {
                    "filename": output_filename,
                    "device_id": device_id,
                    "device_name": device_name,
                    "client_ip": client_ip,
                    "created_at": time.time(),
                    "duration": round(total_duration, 1),
                    "file_size_mb": file_size_mb,
                    "niche_id": niche_id,
                    "layout_mode": layout_mode,
                    "audio_mode": audio_mode,
                    "mute_original": should_mute_original,
                    "voice_id": voice_id or "",
                    "title": clean_title,
                    "subtitle_text": full_summary[:120],
                    "description": description,
                    "pinned_comment": pinned_comment,
                    "amazon_product": amazon_prod,
                    "source_url": source_url,
                    "author": author,
                    "mode": mode,
                    "is_mashup": True,
                    "clips_count": len(clips),
                }

                qa_report = preflight_repurpose_qa(
                    output_path_str,
                    str(ass_path) if (ass_path and ass_path.exists()) else None,
                    meta_data
                )
                meta_data["qa_report"] = qa_report

                try:
                    with open(meta_file, "w", encoding="utf-8") as mf:
                        json.dump(meta_data, mf, ensure_ascii=False, indent=2)
                except Exception as e_meta:
                    logger.warning(f"[Mashup] Lỗi lưu meta_file: {e_meta}")

                return {
                    "success": True,
                    "output_path": output_path_str,
                    "relative_url": f"/media/repurposer/clips/{output_filename}",
                    "filename": output_filename,
                    "duration": round(total_duration, 1),
                    "file_size_mb": file_size_mb,
                    "clips_count": len(clips),
                    "niche_id": niche_id,
                    "layout_mode": layout_mode,
                    "audio_mode": audio_mode,
                    "title": clean_title,
                    "description": description,
                    "pinned_comment": pinned_comment,
                    "amazon_product": amazon_prod,
                    "qa_report": qa_report,
                }
            finally:
                for tf in temp_files:
                    try:
                        if tf.exists():
                            tf.unlink()
                    except Exception:
                        pass

    # ── 5. QUÉT DANH SÁCH VIDEO CÓ SẴN TRONG HỆ THỐNG ĐỂ TÁI CHẾ ─────────────

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


repurposer_service = VideoRepurposerService()
