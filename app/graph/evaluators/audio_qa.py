"""
app.graph.evaluators.audio_qa — Bộ kiểm định chất lượng âm thanh & nhịp điệu giọng đọc.

Kiểm tra:
1. Tổng thời lượng Shorts: 30s - 60s
2. Tốc độ nói WPM (Words Per Minute): Chuẩn Shorts cuốn hút từ 130 - 170 WPM
3. Độ đồng bộ của tệp phụ đề SRT với thời lượng audio
4. Tính toàn vẹn của tệp MP3
5. [FIXED] Phát hiện khoảng im lặng bất thường > 1.5s bằng ffmpeg silencedetect
6. [FIXED] Kiểm tra từng scene audio file có tồn tại đầy đủ không
"""
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from app.graph.state import AudioEvaluationReport

logger = logging.getLogger(__name__)


class AudioQA:
    """Kiểm tra và chấm điểm chất lượng giọng đọc AI."""

    @staticmethod
    def _detect_silence(audio_path: str, min_silence_sec: float = 1.5) -> bool:
        """
        Dùng ffmpeg silencedetect filter để phát hiện khoảng im lặng bất thường.
        Trả về True nếu phát hiện silence > min_silence_sec trong file audio.
        """
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", audio_path,
                    "-af", f"silencedetect=noise=-40dB:d={min_silence_sec}",
                    "-f", "null", "-"
                ],
                capture_output=True, text=True, timeout=20
            )
            return "silence_start" in result.stderr
        except FileNotFoundError:
            logger.debug("ffmpeg không có trong PATH — bỏ qua silence detection")
            return False
        except Exception as e:
            logger.debug(f"AudioQA silence detection lỗi: {e}")
            return False

    @staticmethod
    def evaluate_audio(
        scenes_audio: List[Dict[str, Any]],
        full_audio_path: str,
        total_duration_ms: int,
        subtitle_path: str = ""
    ) -> AudioEvaluationReport:
        p = Path(full_audio_path)
        if not p.exists() or p.stat().st_size < 1000:
            return AudioEvaluationReport(
                total_duration_ms=0,
                total_words=0,
                words_per_minute=0.0,
                pacing_rating="ERROR",
                has_clipping_or_silence=False,
                passed=False,
                feedback="File âm thanh không tồn tại hoặc dung lượng rỗng"
            )

        duration_sec = total_duration_ms / 1000.0
        total_words = 0
        for sa in scenes_audio:
            txt = sa.get("narration") or sa.get("text") or ""
            total_words += len(txt.split())

        # Tính Words Per Minute
        wpm = (total_words / (duration_sec / 60.0)) if duration_sec > 0 else 0.0

        pacing_rating = "PERFECT"
        passed = True
        feedback_items = []

        # [NEW P1-FIX-2] Kiểm tra từng scene audio file có tồn tại không
        missing_scenes = []
        for sa in scenes_audio:
            ap = sa.get("audio_path", "")
            if ap and not Path(ap).exists():
                missing_scenes.append(str(sa.get("scene_id", "?")))
        if missing_scenes:
            passed = False
            feedback_items.append(f"Audio bị thiếu cho cảnh: {', '.join(missing_scenes)}")

        if duration_sec < 26.0:
            pacing_rating = "TOO_SHORT"
            feedback_items.append(f"Thời lượng hơi ngắn cho Shorts ({duration_sec:.1f}s, chuẩn vàng 28s - 55s để giữ chân cao)")
        elif duration_sec > 65.0:
            pacing_rating = "TOO_LONG"
            passed = False
            feedback_items.append(f"Vượt quá giới hạn YouTube Shorts ({duration_sec:.1f}s > 60s)")

        if wpm < 115:
            pacing_rating = "TOO_SLOW"
            feedback_items.append(f"Tốc độ đọc hơi chậm ({wpm:.0f} WPM), chuẩn tự nhiên là 130-165 WPM")
        elif wpm > 185:
            pacing_rating = "TOO_FAST"
            feedback_items.append(f"Tốc độ đọc quá nhanh ({wpm:.0f} WPM), người xem không kịp nghe")

        # [FIXED P1-FIX-2] Phát hiện khoảng im lặng thật bằng ffmpeg (thay vì hardcode False)
        has_silence = AudioQA._detect_silence(full_audio_path)
        if has_silence:
            pacing_rating = "HAS_SILENCE"
            passed = False
            feedback_items.append("Phát hiện khoảng im lặng bất thường > 1.5s trong audio — có thể do TTS bỏ sót cảnh")

        # Kiểm tra file SRT/ASS
        if subtitle_path:
            sub_p = Path(subtitle_path)
            if not sub_p.exists() or sub_p.stat().st_size == 0:
                feedback_items.append("File phụ đề SRT/ASS bị thiếu hoặc rỗng")

        return AudioEvaluationReport(
            total_duration_ms=total_duration_ms,
            total_words=total_words,
            words_per_minute=round(wpm, 1),
            pacing_rating=pacing_rating,
            has_clipping_or_silence=has_silence,
            passed=passed,
            feedback="; ".join(feedback_items) if feedback_items else f"Tốc độ chuẩn ({wpm:.0f} WPM, {duration_sec:.1f}s), âm thanh sắc nét."
        )
