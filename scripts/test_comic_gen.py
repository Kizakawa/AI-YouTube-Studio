"""
Test sinh ảnh nhân vật hoạt hình 2D vẽ nét đơn giản (minimalist comic line-art)
đúng phong cách bóc trần tâm lý / châm biếm
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

from app.agents.image_agent import ImageAgent

def main():
    agent = ImageAgent()
    out_dir = Path("storage/temp/test_comic_art")
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = (
        "Minimalist 2D comic illustration, simple relatable stick-figure-like character with expressive face, "
        "sitting at an office desk looking utterly exhausted, wearing a fake smiling mask on hand, "
        "clean black line art, subtle flat pastel colors, satirical webtoon aesthetic, vertical 9:16 composition, no watermark, no text"
    )

    out_file = str(out_dir / "exhausted_worker_mask.jpg")
    print(f"🎨 Đang sinh ảnh thử nghiệm: '{prompt}'...")
    res = agent.generate_scene_image(
        prompt=prompt,
        output_path=out_file,
        aspect_ratio="9:16",
        niche="general"
    )
    if res and Path(res).exists():
        print(f"✅ Sinh ảnh thành công: {res} ({Path(res).stat().st_size:,} bytes)")
    else:
        print("❌ Lỗi sinh ảnh!")

if __name__ == "__main__":
    main()
