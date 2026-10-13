import sys
import asyncio
import edge_tts

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

async def test_text(txt):
    try:
        c = edge_tts.Communicate(txt, "vi-VN-NamMinhNeural")
        await c.save("storage/temp/test_out.mp3")
        print("SUCCESS:", txt[:35])
    except Exception as e:
        print("ERROR:", txt[:35], "=>", type(e).__name__, e)

async def main():
    await test_text("Xin chao viet nam")
    await test_text("Bạn có bao giờ tự hỏi")
    await test_text("Sếp giao việc ngoài giờ thì gật đầu")
    await test_text("Sự thật tàn nhẫn: Sự tử tế không có ranh giới... người ta không gọi là hiền lành, mà gọi là dễ bị bắt nạt.")

if __name__ == "__main__":
    asyncio.run(main())
