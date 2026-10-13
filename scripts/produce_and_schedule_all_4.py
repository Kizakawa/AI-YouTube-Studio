"""
Master Orchestration Script: Sản xuất & Hẹn giờ phát sóng video cho 4 kênh YouTube.
- 2 Kênh Mỹ (EN): Paws & Hearts Rescue, Wild Odyssey
- 2 Kênh Brazil (PT-BR): Odisseia Selvagem, Mistérios do Mundo

Tự động:
1. Kiểm tra đánh giá kịch bản (Script QA >= 80/100).
2. Dựng video & Thẩm định chất lượng video (FFprobe QA).
3. Gắn link sản phẩm Amazon Affiliate phù hợp từng ngách.
4. Kêu gọi CTA (subscribe + comment) trong kịch bản, mô tả và bình luận ghim.
5. Hẹn giờ khung vàng ngày mai (nếu ngày mai đã có video thì tự động đẩy sang ngày tiếp theo).
"""

import sys
import os
import asyncio
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("ProduceAll4Master")

from scripts.produce_and_schedule_paws_rescue import main as run_paws_rescue
from scripts.produce_and_schedule_wild_odyssey import main as run_wild_odyssey
from scripts.produce_and_schedule_odisseia_selvagem import main as run_odisseia_selvagem
from scripts.produce_and_schedule_misterios_do_mundo import main as run_misterios_do_mundo

CHANNEL_RUNNERS = [
    ("ch_paws_hearts_rescue", "Paws & Hearts Rescue (🇺🇸 US)", run_paws_rescue),
    ("ch_wild_odyssey", "Wild Odyssey (🇺🇸 US)", run_wild_odyssey),
    ("ch_odisseia_selvagem", "Odisseia Selvagem (🇧🇷 Brazil)", run_odisseia_selvagem),
    ("ch_misterios_do_mundo", "Mistérios do Mundo (🇧🇷 Brazil)", run_misterios_do_mundo),
]


async def run_pipeline(selected_channel: str = "all", dry_run: bool = False):
    print("=" * 80)
    print("🚀 BẮT ĐẦU QUY TRÌNH SẢN XUẤT & ĐĂNG VIDEO 4 KÊNH YOUTUBE SHORTS")
    print(f"⏰ Thời điểm bắt đầu: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"⚙️ Chế độ: {'[DRY-RUN] KIỂM THỬ' if dry_run else '[FULL PRODUCTION] SẢN XUẤT & UPLOAD THỰC TẾ'}")
    print("=" * 80)

    results = []

    for cid, cname, runner in CHANNEL_RUNNERS:
        if selected_channel != "all" and selected_channel not in cid:
            continue

        print(f"\n{'#' * 80}")
        print(f"▶️ BẮT ĐẦU XỬ LÝ: {cname.upper()}")
        print(f"{'#' * 80}\n")

        try:
            res = await runner(dry_run=dry_run)
            results.append(res or {"channel": cname, "status": "COMPLETED"})
            print(f"\n✅ {cname} ĐÃ HOÀN TẤT THÀNH CÔNG!\n")
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý {cname}: {e}", exc_info=True)
            results.append({"channel": cname, "status": "FAILED", "error": str(e)})

    print("\n" + "=" * 80)
    print("📋 TỔNG KẾT KẾT QUẢ SẢN XUẤT 4 KÊNH")
    print("=" * 80)
    for r in results:
        ch = r.get("channel", "N/A")
        tt = r.get("title", "N/A")
        sqa = r.get("script_score", "N/A")
        vqa = r.get("video_score", "N/A")
        st = r.get("scheduled_time", "N/A")
        url = r.get("youtube_url", "Chưa có")
        err = r.get("error")
        if err:
            print(f"❌ {ch}: THẤT BẠI - {err}")
        else:
            print(f"✅ {ch}:")
            print(f"   • Tiêu đề: {tt}")
            print(f"   • QA Kịch bản: {sqa}/100 | QA Video: {vqa}/100")
            print(f"   • Lịch phát sóng: {st}")
            if url and url != "Chưa có":
                print(f"   • Shorts URL: {url}")
            print("-" * 50)
    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master producer for 4 channels")
    parser.add_argument("--channel", type=str, default="all", help="Target channel: paws, wild, odisseia, misterios, or all")
    parser.add_argument("--dry-run", action="store_true", help="Test script generation, QA and slot without rendering/uploading")
    args = parser.parse_args()

    asyncio.run(run_pipeline(selected_channel=args.channel, dry_run=args.dry_run))
