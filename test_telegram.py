import sys
import os
import asyncio
from dotenv import load_dotenv

# Đảm bảo in tiếng Việt và emoji không bị lỗi trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv(".env")

async def test_telegram_connection():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    print("=" * 60)
    print("KIEM TRA KET NOI TELEGRAM BOT")
    print("=" * 60)

    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print("[!] LỖI: Chưa có TELEGRAM_BOT_TOKEN trong file .env!")
        print("-> Hãy mở file .env và điền Token bạn nhận được từ @BotFather.")
        print("=" * 60)
        return False

    try:
        from telegram import Bot
        bot = Bot(token=bot_token)
        me = await bot.get_me()
        print("[OK] KẾT NỐI TELEGRAM THÀNH CÔNG 100%!")
        print(f"• Tên Bot: {me.first_name}")
        print(f"• Username Bot: @{me.username}")
        print(f"• Bot ID: {me.id}")
        print(f"• Link chat trực tiếp: https://t.me/{me.username}")
        print("=" * 60)
        return True
    except Exception as e:
        print(f"[!] KẾT NỐI THẤT BẠI: {e}")
        print("-> Hãy kiểm tra lại xem Token trong .env đã chính xác chưa.")
        print("=" * 60)
        return False

if __name__ == "__main__":
    asyncio.run(test_telegram_connection())
