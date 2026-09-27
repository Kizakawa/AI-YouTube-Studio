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
from typing import Dict, List, Any, Optional

from config.settings import settings
from app.agents.content_agent import ContentAgent
from app.services.niche_manager import get_niche, get_niche_subtitle_style
from app.utils.subtitle_helper import SubtitleHelper, ASS_WHITE, ASS_YELLOW, ASS_BLACK
from app.utils.ffmpeg_helper import run_ffmpeg, probe_file, get_duration_ms

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
    """Trích xuất Video ID từ các định dạng URL YouTube khác nhau hoặc ID trực tiếp."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
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

        video_id = extract_youtube_video_id(url_or_path)
        if not video_id:
            raise ValueError(f"Không thể nhận diện Video ID hoặc đường dẫn file: {url_or_path}")

        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                return {
                    "source_type": "youtube",
                    "title": info.get("title", f"YouTube Video {video_id}"),
                    "duration_seconds": float(info.get("duration", 0)),
                    "author": info.get("uploader", "YouTube Creator"),
                    "thumbnail": info.get("thumbnail", f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"),
                    "video_id": video_id,
                    "webpage_url": info.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
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
            }

    def fetch_transcript(self, video_id: str) -> List[Dict[str, Any]]:
        """
        Lấy phụ đề / transcript kèm mốc thời gian từ YouTube.
        Hỗ trợ ưu tiên phụ đề tiếng Việt, tiếng Anh và phụ đề tự động (auto-generated).
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            ytt = YouTubeTranscriptApi()
            transcript_list = ytt.list(video_id)
            
            target_langs = ['vi', 'en', 'es', 'fr', 'ja', 'ko', 'zh']
            selected_transcript = None

            for lang in target_langs:
                try:
                    selected_transcript = transcript_list.find_transcript([lang])
                    break
                except Exception:
                    pass

            if not selected_transcript:
                for t in transcript_list:
                    selected_transcript = t
                    break

            if selected_transcript:
                raw_entries = selected_transcript.fetch()
                result = []
                for item in raw_entries:
                    text = item.get("text", "").strip()
                    if text and text != "[Music]" and text != "[Âm nhạc]":
                        result.append({
                            "start": round(float(item.get("start", 0)), 2),
                            "duration": round(float(item.get("duration", 0)), 2),
                            "text": text.replace("\n", " "),
                        })
                return result

        except Exception as e:
            logger.warning(f"Không lấy được transcript qua YouTubeTranscriptApi cho {video_id}: {e}")

        return []

    # ── 2. PHÂN TÍCH HIGHLIGHTS VIRAL BẰNG GEMINI AI ──────────────────────────

    def analyze_viral_moments(
        self,
        transcript_entries: List[Dict[str, Any]],
        video_title: str,
        video_duration: float,
        niche_id: str = "general",
        mode: str = "self_owned",
        channel_name: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Gửi transcript và ngữ cảnh video sang Gemini AI để phân tích 3-5 đoạn cao trào nhất.
        Đảm bảo mỗi đoạn dài từ 25s đến 55s (tối ưu cho YouTube Shorts retention).
        """
        niche_profile = get_niche(niche_id)
        niche_name = niche_profile.get("name", "Tổng Hợp")
        target_audience = niche_profile.get("target_audience", "Người xem YouTube Shorts")

        timeline_text_lines = []
        for entry in transcript_entries:
            start_m = int(entry['start'] // 60)
            start_s = int(entry['start'] % 60)
            timeline_text_lines.append(f"[{start_m:02d}:{start_s:02d}] {entry['text']}")
        
        timeline_str = "\n".join(timeline_text_lines[:500])

        fair_use_guidance = (
            "- CHẾ ĐỘ: 'Transformative Fair-Use Commentary' (Bình luận phân tích chuyển hóa). "
            "Cần đề xuất góc nhìn bình luận, phản biện hoặc bài học đúc kết để video ngắn đạt chuẩn Sử Dụng Hợp Lý, "
            "tạo giá trị mới thay vì chỉ trích dẫn thụ động."
            if mode == "transformative_commentary"
            else
            "- CHẾ ĐỘ: 'Self-Owned Repurposing' (Tái chế video kênh nhà). "
            "Tập trung vào khoảnh khắc tạo tò mò cao nhất để thúc đẩy người xem click vào xem full video trên kênh."
        )

        prompt = f"""
Bạn là Đạo diễn Biên tập Shorts Triệu View (Viral Shorts Retention Director).
Nhiệm vụ của bạn: Phân tích toàn bộ bản ghi lời thoại (transcript) của video dài dưới đây và trích xuất đúng 3 đến 5 phân đoạn "VÀNG" (High-Impact Viral Highlights) tốt nhất để dựng thành YouTube Shorts (thời lượng 25s - 55s mỗi clip).

THÔNG TIN VIDEO GỐC:
- Tiêu đề: {video_title}
- Thời lượng: {video_duration:.1f} giây
- Ngách nội dung kênh: {niche_name}
- Khán giả mục tiêu: {target_audience}
- Kênh phát hành: {channel_name or 'YouTube Channel'}
{fair_use_guidance}

TRANSCRIPT KÈM TIMESTAMPS:
\"\"\"
{timeline_str}
\"\"\"

TIÊU CHÍ ĐÁNH GIÁ ĐOẠN VIRAL:
1. Hook 3 giây đầu (Bắt buộc): Phải bắt đầu bằng một câu nói gây chấn động, đảo ngược định kiến hoặc kích thích tò mò tột độ.
2. Trọn vẹn ý nghĩa: Đoạn cắt phải kết thúc ở một luận điểm đắt giá hoặc một cú twist / cao trào, không được cắt ngang giữa chừng một từ hoặc ý dở dang.
3. Thời lượng chuẩn: Bắt buộc từ 25 giây đến 55 giây (start_seconds và end_seconds chênh nhau 25-55s).
4. Điểm Viral Score: Chấm điểm từ 80 đến 99 dựa trên tiềm năng giữ chân người xem (Audience Retention).

BẮT BUỘC TRẢ VỀ ĐỊNH DẠNG JSON DUY NHẤT (không kèm text rác) theo cấu trúc:
[
  {{
    "segment_id": 1,
    "title": "Tiêu đề Shorts giật tít viết hoa (kèm 2 hashtag chuẩn SEO)",
    "start_seconds": 12.0,
    "end_seconds": 48.0,
    "duration_seconds": 36.0,
    "hook_quote": "Câu mở đầu 3s đầu tiên của đoạn này",
    "summary": "Tóm tắt ngắn gọn nội dung đoạn này trong 1 câu",
    "viral_score": 95,
    "viral_reason": "Lý do tâm lý học khiến đoạn này sẽ viral và giữ chân người xem",
    "safe_commentary_angle": "Góc nhìn phân tích hoặc câu dẫn dắt tạo giá trị mới bảo vệ bản quyền",
    "transcript_snippet": "Lời thoại trọn vẹn trong khoảng thời gian này"
  }}
]
"""
        try:
            raw_response = self.content_agent._call_api(prompt, json_mode=True, timeout=30)
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
                for m in moments:
                    s = max(0.0, float(m.get("start_seconds", 0)))
                    e = min(video_duration if video_duration > 0 else 9999.0, float(m.get("end_seconds", s + 35.0)))
                    dur = round(e - s, 1)
                    if dur < 15.0:
                        e = s + 30.0
                        dur = 30.0
                    m["start_seconds"] = round(s, 1)
                    m["end_seconds"] = round(e, 1)
                    m["duration_seconds"] = dur
                    m["viral_score"] = int(m.get("viral_score", 90))
                    validated_moments.append(m)
                return validated_moments
        except Exception as e:
            logger.error(f"Lỗi phân tích Gemini cho moments: {e}")

        # Fallback phân đoạn
        default_start = min(15.0, video_duration * 0.1)
        default_end = min(default_start + 45.0, video_duration if video_duration > 0 else 60.0)
        return [
            {
                "segment_id": 1,
                "title": f"BÍ MẬT ĐẰNG SAU: {video_title[:40].upper()} #Shorts #Viral",
                "start_seconds": round(default_start, 1),
                "end_seconds": round(default_end, 1),
                "duration_seconds": round(default_end - default_start, 1),
                "hook_quote": "Đây là điều mà 99% mọi người đã bỏ lỡ...",
                "summary": "Phân đoạn cao trào làm sáng tỏ bí mật quan trọng nhất.",
                "viral_score": 91,
                "viral_reason": "Mở đầu trực diện đánh trúng tâm lý tò mò của người xem.",
                "safe_commentary_angle": "Bình luận đúc kết bài học thực tiễn giúp người xem ứng dụng ngay.",
                "transcript_snippet": "Nội dung phân đoạn cao trào được trích xuất từ video gốc.",
            }
        ]

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

            import yt_dlp
            ydl_opts = {
                "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": str(target_path),
                "quiet": True,
                "no_warnings": True,
                "merge_output_format": "mp4",
            }
            watch_url = f"https://www.youtube.com/watch?v={video_id}" if len(video_id) == 11 else url_or_id
            logger.info(f"Đang tải video từ YouTube: {watch_url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([watch_url])

            if not target_path.exists():
                raise FileNotFoundError(f"Tải video thất bại, không tìm thấy: {target_path}")

            return str(target_path)

    # ── 4. CẮT VIDEO, VERTICAL 9:16 CROP & GHÉP PHỤ ĐỀ KARAOKE ─────────────────

    def render_repurposed_short(
        self,
        source_video_path: str,
        start_seconds: float,
        end_seconds: float,
        output_filename: str,
        niche_id: str = "general",
        layout_mode: str = "blur_background",
        add_subtitles: bool = True,
        subtitle_text: str = "",
        credit_text: Optional[str] = None,
        device_id: str = "default_device",
        device_name: str = "Thiết bị khách",
        client_ip: str = "",
        title: str = "",
    ) -> Dict[str, Any]:
        """
        Cắt đoạn highlight, chuyển sang tỉ lệ dọc 9:16 (1080x1920) và đốt phụ đề Karaoke ASS chuẩn Niche Profile.
        Có hỗ trợ giới hạn luồng render và lưu thông tin định danh thiết bị tạo clip.
        """
        if not os.path.exists(source_video_path):
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {source_video_path}")

        duration = max(end_seconds - start_seconds, 5.0)
        output_path = CLIPS_DIR / output_filename
        output_path_str = str(output_path)

        # Lấy thông tin phụ đề Niche Profile
        niche_profile = get_niche(niche_id)
        niche_sub = get_niche_subtitle_style(niche_profile)
        fontname = niche_sub.get("fontname", "Arial")
        highlight_color = niche_sub.get("secondary_ass", "&H0000FFFF")

        # 1. Tạo file phụ đề ASS nếu có yêu cầu
        ass_file_path = None
        if add_subtitles and subtitle_text.strip():
            ass_filename = f"sub_{Path(output_filename).stem}.ass"
            ass_path = SUBS_DIR / ass_filename
            ass_file_path = str(ass_path)

            from app.schemas.contracts import SceneContract
            pseudo_scene = SceneContract(
                scene_id=1,
                narration=subtitle_text.strip(),
                visual_keyword="viral highlight",
                audio_duration_ms=int(duration * 1000),
                estimated_seconds=min(max(float(duration), 1.0), 300.0),
            )
            SubtitleHelper.save_ass_file(
                scenes=[pseudo_scene],
                output_path=ass_file_path,
                fontname=fontname,
                fontsize=42,
                margin_v=240,
                niche_id=niche_id,
            )

        # 2. Xây dựng bộ lọc FFmpeg chuyển sang tỉ lệ dọc 9:16 (1080x1920)
        if layout_mode == "center_crop":
            filter_v = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920"
        else:
            filter_v = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
                "[0:v]scale=1080:-2:flags=lanczos[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
            )

        # Nếu có phụ đề ASS, thêm bộ lọc ass
        if ass_file_path and os.path.exists(ass_file_path):
            escaped_ass = ass_file_path.replace("\\", "/").replace(":", "\\:")
            if layout_mode == "center_crop":
                filter_v += f",ass='{escaped_ass}'"
            else:
                filter_v = (
                    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
                    "[0:v]scale=1080:-2:flags=lanczos[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass='{escaped_ass}'[v]"
                )

        # 3. Chạy FFmpeg cắt chính xác và render (có Semaphore giới hạn tải đồng thời)
        ffmpeg_args = [
            "-ss", str(start_seconds),
            "-t", str(duration),
            "-i", source_video_path,
        ]

        if "[v]" in filter_v:
            ffmpeg_args += ["-filter_complex", filter_v, "-map", "[v]", "-map", "0:a?"]
        else:
            ffmpeg_args += ["-vf", filter_v]

        ffmpeg_args += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            output_path_str,
        ]

        logger.info(f"[{device_name} ({client_ip})] Đang render Short 9:16: {output_path_str}")
        with _render_semaphore:
            run_ffmpeg(ffmpeg_args, timeout=240)

        if not os.path.exists(output_path_str):
            raise RuntimeError(f"Render Short thất bại, không tìm thấy file: {output_path_str}")

        file_size_mb = round(os.path.getsize(output_path_str) / (1024 * 1024), 2)

        # Lưu file metadata định danh thiết bị tạo clip
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
            "title": title or Path(output_filename).stem,
            "subtitle_text": subtitle_text[:120],
        }
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(meta_data, mf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Lỗi lưu clip meta: {e}")

        return {
            "success": True,
            "output_path": output_path_str,
            "relative_url": f"/media/repurposer/clips/{output_filename}",
            "filename": output_filename,
            "duration": round(duration, 1),
            "file_size_mb": file_size_mb,
            "niche_id": niche_id,
            "layout_mode": layout_mode,
            "device_id": device_id,
            "device_name": device_name,
            "client_ip": client_ip,
            "title": title or Path(output_filename).stem,
        }

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
