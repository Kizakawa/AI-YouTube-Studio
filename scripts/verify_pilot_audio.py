import sys
import asyncio
import edge_tts
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

async def main():
    scenes = [
        "Bạn có bao giờ tự hỏi: Tại sao bạn càng ngoan ngoãn, biết điều, người khác lại càng đối xử tệ với bạn?",
        "Sếp giao việc ngoài giờ, bạn gật đầu. Bạn bè mượn tiền, bạn cười trừ. Người khác ép bạn, bạn lại tự nhận lỗi về mình.",
        "Sự thật tàn nhẫn: Sự tử tế không có ranh giới, người ta không gọi là hiền lành, mà là tự biến mình thành kẻ yếu thế.",
        "Học cách nói Không không biến bạn thành kẻ xấu, nó chỉ nhắc người khác nhớ rằng: Bạn cũng là một con người!"
    ]
    out_dir = Path("storage/temp/test_pilot_audio")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(scenes):
        dest = str(out_dir / f"scene_{i+1:02d}.mp3")
        c = edge_tts.Communicate(s, "vi-VN-NamMinhNeural")
        await c.save(dest)
        sz = Path(dest).stat().st_size
        print(f"✅ Scene {i+1} OK: {sz:,} bytes")

if __name__ == "__main__":
    asyncio.run(main())
