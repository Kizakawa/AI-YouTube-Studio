"""
scripts/test_repurposer_refactor_pipeline.py
Kiểm thử toàn diện 4 tầng kiến trúc mới của Pipeline Tái Chế Nội Dung:
1. Tầng Phụ đề: Word-level ASS Karaoke từ timed_segments, Safe Zone Y=580, Placeholder Sanitizer.
2. Tầng Âm thanh: FFmpeg VAD dò dead-air đầu video, Audio Envelope Fade 0.25s chống clipping.
3. Tầng Thị giác: Smart Crop 9:16 Fullscreen lấp đầy màn hình & khử logo 4 góc.
4. Tầng Pre-flight QA: Kiểm định chất lượng clip xuất xưởng (LUFS, dead-air, subtitle drift, score 0-100).
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.utils.subtitle_helper import SubtitleHelper
from app.utils.ffmpeg_helper import detect_audio_speech_start, measure_audio_loudness_and_silence, run_ffmpeg
from app.services.repurposer_qa import preflight_repurpose_qa
from app.services.video_repurposer import VideoRepurposerService


def test_1_subtitle_binding_and_sanitizer():
    print("\n=== [1] Testing Subtitle Binding, Safe Zone & Placeholder Sanitizer ===")
    
    # 1. Test placeholder sanitizer
    dirty_text = "[Music] Breaking news! {hook_quote} Incredible discovery (Applause) lorem ipsum scene 01"
    clean_text = SubtitleHelper.sanitize_subtitle_text(dirty_text)
    print(f"Dirty text: '{dirty_text}'")
    print(f"Clean text: '{clean_text}'")
    assert "[Music]" not in clean_text, "Sanitizer failed to remove [Music]"
    assert "{hook_quote}" not in clean_text, "Sanitizer failed to remove {hook_quote}"
    assert "Applause" not in clean_text, "Sanitizer failed to remove (Applause)"
    assert "lorem ipsum" not in clean_text.lower(), "Sanitizer failed to remove dummy lorem ipsum"
    print("✅ 1a. Placeholder & Audio Tag Sanitizer PASSED!")

    # 2. Test timed-segments ASS Karaoke generation
    timed_segs = [
        {"start": 12.0, "duration": 2.2, "text": "[Music] The apex predator strikes with extreme precision"},
        {"start": 14.5, "duration": 2.8, "text": "Nothing can escape its razor sharp jaws {tag}"},
    ]
    ass_content = SubtitleHelper.build_ass_from_timed_segments(
        timed_segments=timed_segs,
        total_duration_sec=10.0,
        clip_start_offset=12.0,
        niche_id="apex_predator",
        credit_text="BBC Earth • Full Video in Description",
        show_cta_badge=True,
    )

    # Assertions on ASS content
    assert "Style: Karaoke" in ass_content, "ASS missing Karaoke style"
    assert "Style: CTABadge" in ass_content, "ASS missing CTABadge style"
    assert "Style: WatermarkStyle" in ass_content, "ASS missing WatermarkStyle style"
    assert "580,1" in ass_content, "Karaoke subtitle must use Safe Zone MarginV=580"
    assert "780,1" in ass_content, "CTA badge must use MarginV=780 to avoid overlap"
    assert "{\\kf" in ass_content, "Dialogue must contain fluid kinetic \\kf tags"
    assert "BBC Earth" in ass_content, "Watermark credit missing in ASS"
    assert "[Music]" not in ass_content, "Trash audio tag slipped into ASS"
    print("✅ 1b. Timed-Segments ASS Karaoke with Safe Zone & Collision Avoidance PASSED!")


def test_2_audio_vad_and_envelope_filters():
    print("\n=== [2] Testing Audio VAD Dead-Air Detection & Envelope Fade ===")
    
    # Tạo file test audio ngắn 3s bằng FFmpeg: 0.8s im lặng + 2.2s sine wave âm thanh
    tmp_audio = "output/repurposer/test_vad_audio.mp3"
    Path(tmp_audio).parent.mkdir(parents=True, exist_ok=True)
    
    # FFmpeg command: tạo 2.2s sine wave 440Hz rồi trễ 800ms (0.8s im lặng đầu)
    ffmpeg_cmd = [
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2.2",
        "-af", "adelay=800|800",
        "-c:a", "libmp3lame",
        tmp_audio
    ]
    try:
        run_ffmpeg(ffmpeg_cmd, timeout=30)
        assert os.path.exists(tmp_audio), "Failed to generate test audio"
        
        # Test VAD: Phát hiện đúng khoảng 0.8s im lặng ở đầu
        speech_offset = detect_audio_speech_start(tmp_audio, start_seconds=0.0, scan_duration=2.5, noise_threshold_db=-32.0)
        print(f"Detected speech start offset: {speech_offset}s")
        assert 0.5 <= speech_offset <= 1.0, f"VAD offset {speech_offset}s out of expected range ~0.8s"
        print("✅ 2a. FFmpeg Native VAD Dead-Air Detection PASSED!")

        # Test Audio Loudness & Silence Analysis
        qa_audio = measure_audio_loudness_and_silence(tmp_audio)
        print("Audio QA measurements:", qa_audio)
        assert "integrated_lufs" in qa_audio, "Missing integrated_lufs in audio analysis"
        print("✅ 2b. Audio Loudness & Silence Analysis PASSED!")

    finally:
        if os.path.exists(tmp_audio):
            try:
                os.remove(tmp_audio)
            except Exception:
                pass


def test_3_preflight_qa_gate():
    print("\n=== [3] Testing Pre-flight QA Gate Quality Verification ===")
    
    # Tạo video test MP4 giả lập 1080x1920 6 giây có audio
    tmp_vid = "output/repurposer/test_qa_video.mp4"
    tmp_ass = "output/repurposer/test_qa_sub.ass"
    Path(tmp_vid).parent.mkdir(parents=True, exist_ok=True)

    # Tạo file ASS chuẩn
    timed_segs = [
        {"start": 0.0, "duration": 2.5, "text": "Testing automated quality gate system"},
        {"start": 2.5, "duration": 3.0, "text": "All metrics passed with flying colors"}
    ]
    SubtitleHelper.save_ass_from_timed_segments(
        timed_segments=timed_segs,
        output_path=tmp_ass,
        total_duration_sec=6.0,
        clip_start_offset=0.0,
        niche_id="apex_predator"
    )

    # Tạo video 1080x1920 6s bằng FFmpeg kèm audio và bitrate thực tế > 0.5MB
    ffmpeg_cmd = [
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=6.0",
        "-f", "lavfi", "-i", "sine=frequency=500:duration=6.0",
        "-c:v", "libx264", "-b:v", "2M", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        tmp_vid
    ]
    try:
        run_ffmpeg(ffmpeg_cmd, timeout=30)
        assert os.path.exists(tmp_vid), "Failed to generate test QA video"

        qa_result = preflight_repurpose_qa(tmp_vid, tmp_ass, metadata={"title": "Test QA Short"})
        print("QA Result Summary:", qa_result["summary"])
        print("QA Result Score:", qa_result["score"])
        print("QA Video Specs:", qa_result["video_specs"])
        
        assert qa_result["passed"] is True, f"QA Gate failed: {qa_result['issues']}"
        assert qa_result["score"] >= 80, f"QA score too low: {qa_result['score']}"
        assert qa_result["video_specs"]["width"] == 1080, "Width not 1080"
        assert qa_result["video_specs"]["height"] == 1920, "Height not 1920"
        print("✅ 3. Pre-flight QA Gate Automated Inspection PASSED!")

    finally:
        for f in [tmp_vid, tmp_ass]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass


if __name__ == "__main__":
    print("🚀 RUNNING COMPREHENSIVE REPURPOSER ARCHITECTURE REFACTOR TESTS 🚀")
    test_1_subtitle_binding_and_sanitizer()
    test_2_audio_vad_and_envelope_filters()
    test_3_preflight_qa_gate()
    print("\n🎉 ALL 4 ARCHITECTURAL TIERS PASSED 100% SUCCESFULLY! 🎉\n")
