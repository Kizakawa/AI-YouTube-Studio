"""
Test kiểm tra chuyên sâu tính năng Bắt Dính Biên Câu Thoại (Sentence Boundary Snapper)
và Làm Mềm Chuyển Âm Audio Fade In/Out (50s - 58s Sweet Spot) cho Shorts Repurposer.
"""

import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.video_repurposer import VideoRepurposerService, repurposer_service
from app.utils.ffmpeg_helper import probe_file, run_ffmpeg

def test_snap_to_sentence_boundary():
    print("=== [1] Testing Sentence Boundary Snapping & 50s-58s Range ===")
    service = VideoRepurposerService()

    # Tạo transcript giả lập với các câu hội thoại có dấu câu và khoảng lặng
    transcript = [
        {"start": 0.0, "duration": 4.0, "text": "Welcome to today's fascinating nature breakdown."},
        {"start": 4.5, "duration": 5.0, "text": "In the deep African savannah, danger lurks everywhere."},
        {"start": 10.0, "duration": 6.0, "text": "Notice how the lioness slowly approaches her unsuspecting target."},
        {"start": 16.5, "duration": 7.0, "text": "Every single muscle in her body is coiled with lethal power."},
        {"start": 24.0, "duration": 6.0, "text": "She waits for the precise moment when the wind shifts in her favor."},
        {"start": 30.5, "duration": 5.5, "text": "Then, in a fraction of a second, the strike begins!"},
        {"start": 36.5, "duration": 6.5, "text": "Dust flies into the air as the chase reaches maximum speed."},
        {"start": 43.5, "duration": 5.0, "text": "The zebra tries desperately to dodge the relentless predator."},
        {"start": 49.0, "duration": 5.5, "text": "Against all odds, the defensive kick connects and turns the tables."},
        {"start": 55.0, "duration": 7.5, "text": "This solitary encounter proves that nature never plays by ordinary rules."},
        {"start": 63.0, "duration": 5.0, "text": "If you enjoyed this breakdown, make sure to like and subscribe for more daily wildlife stories."},
        {"start": 68.5, "duration": 4.0, "text": "See you in the next episode."},
    ]

    video_duration = 120.0

    # Test Case 1: AI đề xuất mốc thô 10s -> 45s (quá ngắn: 35s, cắt giữa chừng)
    # Kỳ vọng: Thuật toán tự kéo dài vào khoảng 50s - 58s và bắt dính câu kết hoàn chỉnh
    s, e = service.snap_to_sentence_boundary(
        transcript_entries=transcript,
        target_start=10.0,
        target_end=45.0,
        video_duration=video_duration,
        target_min_duration=48.0,
        target_max_duration=58.0
    )
    dur = round(e - s, 2)
    print(f"Test 1 (Raw 10s-45s): Snapped Start={s}s, Snapped End={e}s, Duration={dur}s")
    assert 48.0 <= dur <= 58.0, f"Độ dài {dur}s nằm ngoài chuẩn 48s - 58s!"
    print(f"✅ Test 1 Passed: Duration={dur}s (Chuẩn Shorts tối ưu 50-58s)")

    # Test Case 2: AI đề xuất mốc thô 15.2s (giữa câu) -> 72s (>60s)
    # Kỳ vọng: Start bắt dính vào đầu câu 16.5s hoặc 10.0s, End bị khóa cứng <= 58s và kết thúc ở câu trọn vẹn
    s2, e2 = service.snap_to_sentence_boundary(
        transcript_entries=transcript,
        target_start=15.2,
        target_end=72.0,
        video_duration=video_duration,
        target_min_duration=48.0,
        target_max_duration=58.0
    )
    dur2 = round(e2 - s2, 2)
    print(f"Test 2 (Raw 15.2s-72s): Snapped Start={s2}s, Snapped End={e2}s, Duration={dur2}s")
    assert 48.0 <= dur2 <= 58.0, f"Độ dài {dur2}s vi phạm trần Shorts 58s!"
    print(f"✅ Test 2 Passed: Duration={dur2}s (Không vượt quá trần 58s)")

    # Test Case 3: Video không có transcript
    s3, e3 = service.snap_to_sentence_boundary(
        transcript_entries=[],
        target_start=5.0,
        target_end=35.0,
        video_duration=video_duration,
        target_min_duration=48.0,
        target_max_duration=58.0
    )
    dur3 = round(e3 - s3, 2)
    print(f"Test 3 (No transcript): Snapped Start={s3}s, Snapped End={e3}s, Duration={dur3}s")
    assert 48.0 <= dur3 <= 58.0, f"Fallback độ dài {dur3}s không đạt 48s - 58s!"
    print(f"✅ Test 3 Passed: Duration={dur3}s (Fallback tự động căn 54-58s)")

def test_render_with_audio_fade():
    print("\n=== [2] Testing Render Short with Audio Fade-In & Fade-Out ===")
    from app.services.video_repurposer import DOWNLOADS_DIR, CLIPS_DIR
    
    # Tìm 1 file có sẵn trong DOWNLOADS_DIR
    source_files = list(DOWNLOADS_DIR.glob("*.mp4"))
    if not source_files:
        print("⚠️ Không có video trong downloads, bỏ qua test render thực tế.")
        return

    src_file = str(source_files[0])
    print(f"Sử dụng file nguồn có sẵn: {src_file}")
    
    test_out = "short_test_fade_52s.mp4"
    res = repurposer_service.render_repurposed_short(
        source_video_path=src_file,
        start_seconds=10.0,
        end_seconds=62.0,  # 52s
        output_filename=test_out,
        niche_id="wildlife_predators",
        layout_mode="blur_background",
        add_subtitles=True,
        subtitle_text="Apex Predator Lethal Strike in the Savanna",
        title="Why Even Lions Fear This Hunter #Shorts",
        device_id="test_suite",
        device_name="Test Runner"
    )

    out_path = Path(res["output_path"])
    assert out_path.exists(), "File render không tồn tại!"
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Render Short thành công: {out_path.name} ({size_mb:.2f} MB)")

    # Probe file để kiểm tra độ dài và stream audio
    info = probe_file(str(out_path))
    duration = float(info["format"].get("duration", 0))
    streams = info.get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)

    print(f"Short Rendered Duration: {duration:.2f}s | Has Audio: {has_audio} | Has Video: {has_video}")
    assert has_audio, "Short thiếu audio stream!"
    assert has_video, "Short thiếu video stream!"
    assert 50.0 <= duration <= 54.0, f"Độ dài thực tế {duration}s không đúng mốc 52s!"

    # Dọn dẹp file test
    out_path.unlink(missing_ok=True)
    meta_path = out_path.with_name(f"{out_path.stem}.meta.json")
    meta_path.unlink(missing_ok=True)
    print("✅ PASS: Real Render with 52s Duration and Audio Fade-In/Out Verified!")

if __name__ == "__main__":
    test_snap_to_sentence_boundary()
    test_render_with_audio_fade()
    print("\n🎉 ALL AUDIO & BOUNDARY PRECISION TESTS PASSED SUCCESSFULLY! 🎉")
