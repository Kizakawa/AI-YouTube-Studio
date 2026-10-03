"""
Test End-to-End Render Pipeline cho Ưu tiên 1:
- Tạo kịch bản phân cảnh mẫu (2 scenes)
- Sinh audio Edge-TTS per-scene
- Tải/tạo media 9:16 (Ken Burns effect)
- Tự động sinh file phụ đề hardsub (SubtitleHelper)
- FFmpeg render 1080x1920 + Hardsub burn-in + BGM ducking
- Kiểm tra QA bằng ffprobe (Video, Audio, Resolution, Codec)
"""
import sys
import os
import asyncio
from pathlib import Path

# Đảm bảo UTF-8 cho Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db
from app.database.models import get_db, VideoJob
from app.schemas.contracts import ScriptContract, SceneContract
from app.services.pipeline import VideoPipeline
from app.utils.ffmpeg_helper import validate_output, is_ffmpeg_installed, get_ffmpeg_version
import pytest


@pytest.mark.anyio
async def test_full_pipeline_render():
    print("=" * 70)
    print("🧪 KIỂM THỬ END-TO-END RENDER VIDEO THỰC TẾ (ƯU TIÊN 1)")
    print("=" * 70)

    # 1. Kiểm tra môi trường FFmpeg
    assert is_ffmpeg_installed(), "FFmpeg chưa được cài đặt hoặc chưa nhận diện trong PATH!"
    print(f"✅ FFmpeg sẵn sàng: {get_ffmpeg_version()[:60]}...")

    # 2. Khởi tạo DB
    init_db()

    # 3. Tạo kịch bản mẫu ngắn (2 scenes) để test nhanh & toàn diện
    sample_script = ScriptContract(
        title="3 Bí Mật Tâm Lý Học Thao Túng",
        hook="Bộ não của bạn đang bị kiểm soát ngay lúc này.",
        full_narration="Bộ não của bạn đang bị kiểm soát ngay lúc này. Đây là bí mật tâm lý học bóng tối mà bạn cần biết.",
        scenes=[
            SceneContract(
                scene_id=1,
                narration="Bộ não của bạn đang bị kiểm soát ngay lúc này.",
                visual_keyword="dark mysterious brain neon lights",
                estimated_seconds=3.5,
            ),
            SceneContract(
                scene_id=2,
                narration="Đây là bí mật tâm lý học bóng tối mà bạn cần biết.",
                visual_keyword="shadowy mysterious figure silhouette",
                estimated_seconds=4.0,
            ),
        ],
        tags=["tamly", "shorts", "psychology"],
        description="Khám phá sự thật về tâm lý học kiểm soát tâm trí #shorts #tamly"
    )

    # 4. Lưu job vào Database
    from app.database.models import User
    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 123456).first()
        if not u:
            u = User(telegram_id=123456, username="test_user", full_name="Test User", role="admin")
            db.add(u)
            db.flush()

        job = VideoJob(
            user_id=123456,
            target_channel_id="channel_psychology",
            topic="Bí mật tâm lý học bóng tối",
            title=sample_script.title,
            script=sample_script.full_narration,
            description=sample_script.description,
            tags=",".join(sample_script.tags),
            scenes_json=sample_script.to_json_str(),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.flush()
        job_id = job.id

    print(f"\n[1] Đã tạo VideoJob #{job_id} trong DB")

    # 5. Hàm callback log tiến trình
    async def progress_cb(stage: str, msg: str):
        print(f"   [{stage}] {msg}")

    # 6. Chạy VideoPipeline thực tế
    print("\n[2] Bắt đầu kích hoạt VideoPipeline.execute()...")
    pipeline = VideoPipeline(job_id=job_id, progress_callback=progress_cb)
    render_result = await pipeline.execute()

    # 7. Kiểm tra kết quả Render
    output_path = render_result.output_path
    print(f"\n[3] File đầu ra: {output_path}")
    assert Path(output_path).exists(), f"File video đầu ra không tồn tại: {output_path}"
    file_size_mb = round(Path(output_path).stat().st_size / (1024 * 1024), 2)
    assert file_size_mb > 0.05, f"File quá nhỏ: {file_size_mb} MB"
    print(f"✅ Dung lượng video: {file_size_mb} MB")

    # 8. QA tự động bằng ffprobe
    print("\n[4] Kiểm tra chất lượng kỹ thuật (QA ffprobe)...")
    qa = validate_output(output_path)
    print(f"   • Hợp lệ (valid): {qa['valid']}")
    print(f"   • Độ phân giải: {qa['resolution']}")
    print(f"   • Video Codec: {qa['codec_video']}")
    print(f"   • Audio Codec: {qa['codec_audio']}")
    print(f"   • Thời lượng: {qa['duration_ms']}ms ({qa['duration_ms']/1000:.1f}s)")

    if qa['errors']:
        print(f"   ⚠️ Lỗi phát hiện: {qa['errors']}")

    assert qa["valid"], f"QA kiểm tra thất bại: {qa['errors']}"
    assert qa["resolution"] == "1080x1920", f"Sai độ phân giải 9:16 Shorts: {qa['resolution']}"
    assert qa["has_video"], "Thiếu video stream!"
    assert qa["has_audio"], "Thiếu audio stream!"

    # 9. Kiểm tra file phụ đề SRT đã tạo
    subtitle_file = pipeline.work_dir / "subtitles.srt"
    assert subtitle_file.exists(), "File phụ đề SRT không được tạo!"
    srt_content = subtitle_file.read_text(encoding="utf-8")
    print(f"\n[5] Kiểm tra phụ đề SRT:\n{srt_content[:200]}...")

    # 10. Kiểm tra trạng thái DB
    with get_db() as db:
        saved_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        assert saved_job.status == "RENDERED", f"Status trong DB không phải RENDERED: {saved_job.status}"
        assert saved_job.video_path == output_path, "video_path chưa lưu vào DB!"

    print("\n" + "=" * 70)
    print("🏆 TẤT CẢ KIỂM THỬ RENDER ĐỀU THÀNH CÔNG RỰC RỠ!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_full_pipeline_render())
