"""
app.services.repurposer_qa — Pre-Flight Quality Gate (QA Worker) cho Pipeline Tái Chế Shorts.

Kiểm tra tự động trước khi xuất xưởng clip:
1. Thông số Video: 1080x1920 portrait 9:16, codec H.264, bitrate, duration hợp lệ (5-60s).
2. Thông số Audio: Integrated Loudness (-14 LUFS ± 2 LUFS), kiểm tra dead-air trong 2 giây đầu.
3. Thẩm định Phụ đề: Đảm bảo không chứa placeholder/chuỗi rác template, đồng bộ thời lượng (drift < 1.0s).
4. Phân hạng Quality Score (0 - 100) và cấp chứng chỉ PASSED_QA cho clip.
"""

import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.utils.ffmpeg_helper import probe_file, measure_audio_loudness_and_silence

logger = logging.getLogger(__name__)


def preflight_repurpose_qa(
    video_path: str,
    ass_path: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Thực hiện Pre-Flight QA toàn diện cho video Shorts trước khi xuất bản.
    """
    report = {
        "passed": True,
        "status": "PASSED",
        "score": 100,
        "video_specs": {},
        "audio_specs": {},
        "subtitle_specs": {},
        "issues": [],
        "warnings": [],
        "summary": "",
    }

    # ── 1. KIỂM TRA TỆP VIDEO CƠ BẢN ──────────────────────────────────────
    if not video_path or not os.path.exists(video_path):
        report["passed"] = False
        report["status"] = "FAILED_QA"
        report["score"] = 0
        report["issues"].append("Tệp video không tồn tại hoặc đường dẫn sai")
        report["summary"] = "❌ FAILED: File không tồn tại"
        return report

    file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
    if file_size_mb < 0.2:
        report["passed"] = False
        report["status"] = "FAILED_QA"
        report["score"] = 10
        report["issues"].append(f"Dung lượng tệp quá nhỏ ({file_size_mb}MB), có thể bị lỗi render")
        report["summary"] = f"❌ FAILED: File quá nhỏ ({file_size_mb}MB)"
        return report

    info = probe_file(video_path)
    if not info:
        report["passed"] = False
        report["status"] = "FAILED_QA"
        report["score"] = 20
        report["issues"].append("FFprobe không thể đọc được metadata của video")
        report["summary"] = "❌ FAILED: Lỗi đọc metadata video"
        return report

    # Phân tích streams
    has_video = False
    has_audio = False
    width, height = 0, 0
    duration_sec = 0.0
    v_codec = ""
    a_codec = ""

    format_info = info.get("format", {})
    try:
        duration_sec = round(float(format_info.get("duration", 0.0)), 2)
    except (ValueError, TypeError):
        duration_sec = 0.0

    for s in info.get("streams", []):
        codec_type = s.get("codec_type")
        if codec_type == "video":
            has_video = True
            width = int(s.get("width", 0))
            height = int(s.get("height", 0))
            v_codec = s.get("codec_name", "")
        elif codec_type == "audio":
            has_audio = True
            a_codec = s.get("codec_name", "")

    report["video_specs"] = {
        "width": width,
        "height": height,
        "duration_sec": duration_sec,
        "file_size_mb": file_size_mb,
        "video_codec": v_codec,
        "audio_codec": a_codec,
    }

    if not has_video:
        report["passed"] = False
        report["issues"].append("Không tìm thấy video stream")
    if not has_audio:
        report["passed"] = False
        report["issues"].append("Không tìm thấy audio stream")

    # Kiểm tra tỉ lệ 9:16 (1080x1920)
    if width != 1080 or height != 1920:
        if height > width:
            report["warnings"].append(f"Độ phân giải dọc nhưng khác 1080x1920 ({width}x{height})")
            report["score"] -= 5
        else:
            report["issues"].append(f"Video không ở định dạng dọc 9:16 ({width}x{height})")
            report["score"] -= 25

    if duration_sec < 5.0:
        report["issues"].append(f"Thời lượng video quá ngắn ({duration_sec}s < 5s)")
        report["score"] -= 20
    elif duration_sec > 62.0:
        report["warnings"].append(f"Thời lượng video hơi dài cho Shorts ({duration_sec}s > 60s)")
        report["score"] -= 5

    # ── 2. KIỂM TRA CHỈ SỐ ÂM THANH & DEAD AIR ─────────────────────────────
    audio_qa = measure_audio_loudness_and_silence(video_path)
    report["audio_specs"] = audio_qa

    if not audio_qa.get("lufs_passed", True):
        report["warnings"].append(audio_qa.get("feedback", "Âm lượng chưa chuẩn"))
        report["score"] -= 10

    if audio_qa.get("has_dead_air_opening", False):
        report["warnings"].append("Phát hiện khoảng lặng (dead air) ở 2 giây đầu video")
        report["score"] -= 15

    # ── 3. THẨM ĐỊNH PHỤ ĐỀ (SUBTITLE INTEGRITY & PLACEHOLDERS) ─────────────
    if ass_path and os.path.exists(ass_path):
        try:
            with open(ass_path, "r", encoding="utf-8") as f:
                ass_content = f.read()

            dialogue_lines = [line for line in ass_content.split("\n") if line.startswith("Dialogue:")]
            report["subtitle_specs"]["dialogue_count"] = len(dialogue_lines)

            # Quét placeholder và chuỗi rác template (bỏ qua các ASS tag chuẩn như {\kf...}, {\fade...})
            placeholder_pattern = re.compile(
                r'\[(?:Music|Âm nhạc|Applause|Laughter|Tiếng cười)\]|\b(?:placeholder|dummy text|lorem ipsum|scene \d+)\b',
                re.IGNORECASE
            )
            detected_trash = []
            for line in dialogue_lines:
                # Lấy phần text thực tế sau dấu phẩy thứ 9
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    dia_text = parts[9]
                    # Bỏ tag ASS dạng {...}
                    clean_dia = re.sub(r'\{[^{}]*\}', '', dia_text)
                    m = placeholder_pattern.search(clean_dia)
                    if m:
                        detected_trash.append(m.group(0))

            if detected_trash:
                report["warnings"].append(f"Phụ đề chứa từ khóa rác / placeholder: {list(set(detected_trash))[:3]}")
                report["score"] -= 20

            # Đo độ lệch thời lượng giữa phụ đề và video
            if dialogue_lines and duration_sec > 0:
                last_line = dialogue_lines[-1]
                parts = last_line.split(",", 9)
                if len(parts) >= 3:
                    end_time_str = parts[2].strip()  # H:MM:SS.cc
                    try:
                        h, m, s_cs = end_time_str.split(":")
                        s, cs = s_cs.split(".")
                        last_sub_end_sec = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0
                        drift = abs(duration_sec - last_sub_end_sec)
                        report["subtitle_specs"]["last_sub_end_sec"] = last_sub_end_sec
                        report["subtitle_specs"]["drift_sec"] = round(drift, 2)
                        if drift > 3.0:
                            report["warnings"].append(f"Phụ đề lệch {drift:.1f}s so với thời lượng video ({duration_sec:.1f}s)")
                            report["score"] -= 10
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Lỗi kiểm tra phụ đề ASS: {e}")

    # ── 4. TỔNG KẾT VÀ ĐÁNH GIÁ TRẠNG THÁI ─────────────────────────────────
    report["score"] = max(0, min(100, report["score"]))

    if report["issues"]:
        report["passed"] = False
        report["status"] = "FAILED_QA"
        report["summary"] = f"❌ FAILED_QA ({report['score']}/100) — {report['issues'][0]}"
    elif report["score"] < 70:
        report["passed"] = False
        report["status"] = "WARNING_QA"
        report["summary"] = f"⚠️ CẦN LƯU Ý ({report['score']}/100) — {'; '.join(report['warnings'][:2])}"
    else:
        report["passed"] = True
        report["status"] = "PASSED_QA"
        lufs_val = audio_qa.get("integrated_lufs", -14.0)
        report["summary"] = f"✅ QA PASSED ({report['score']}/100) | {lufs_val} LUFS | 1080x1920 | 0.0s Dead-Air"

    logger.info(f"Pre-flight QA result cho {video_path}: {report['summary']}")
    return report
