"""
Test render clip nodding animation với camera_motion='nod_loop'
"""
import sys
import os
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.utils.ffmpeg_helper import image_to_video

def main():
    img_path = "storage/temp/test_comic_art/exhausted_worker_mask.jpg"
    out_video = "storage/temp/test_comic_art/test_nodding_clip.mp4"

    print("🎬 Đang render thử nghiệm clip gật đầu (nod_loop)...")
    res = image_to_video(
        image_path=img_path,
        duration_seconds=3.5,
        output_path=out_video,
        camera_motion="nod_loop"
    )
    if res and Path(res).exists():
        size = Path(res).stat().st_size
        print(f"✅ Render clip gật đầu THÀNH CÔNG: {res} ({size:,} bytes)")
    else:
        print("❌ Lỗi render clip!")

if __name__ == "__main__":
    main()
