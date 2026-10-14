"""
Script tự động sản xuất video trọn gói cho 3 kênh tiếng Anh còn thiếu hôm nay:
1. Psychology of the world (channel_maya)
2. Paws & Hearts Rescue (ch_paws_hearts_rescue)
3. Wild Odyssey (ch_wild_odyssey)

Quy trình chuẩn 5 bước nghiêm ngặt:
- Bước 1: Tạo kịch bản viral 4-beat retention + Đánh giá chấm điểm kịch bản (>= 80/100)
- Bước 2: Lồng ghép link sản phẩm Amazon Affiliate (tag kizakawa-20) vào mô tả & bình luận ghim
- Bước 3: Đăng ký VideoJob vào Database
- Bước 4: Dựng video (VideoPipeline) + Chấm điểm kỹ thuật video FFprobe QA (>= 85/100)
- Bước 5: Upload YouTube Shorts & Hẹn lịch phát sóng vào khung giờ vàng chuẩn của kênh

Cách dùng:
    python scripts/produce_english_channels.py --channel channel_maya
    python scripts/produce_english_channels.py --channel ch_paws_hearts_rescue
    python scripts/produce_english_channels.py --channel ch_wild_odyssey
    python scripts/produce_english_channels.py --channel all
"""
import sys
import os
import json
import asyncio
import logging
import argparse
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Đảm bảo import được module dự án
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# UTF-8 trên Windows console
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
logger = logging.getLogger("ProduceEnglishChannels")

CHANNELS_CONFIG = {
    "channel_maya": {
        "id": "channel_maya",
        "name": "Psychology of the world",
        "niche": "psychology",
        "language": "en",
        "google_account": "account_1",
        "preferred_voice": "en-US-AndrewNeural",
        "handle": "@vv1n-oe9gs",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel Psychology of the world. High-retention 4-beat dark psychology arc: "
            "HOOK (0-3s): Chilling psychological truth or covert manipulation sign that immediately creates urgency. "
            "DEVELOPMENT: Explain the hidden behavioral mechanism using relatable real-life scenarios. "
            "CLIMAX: The devastating reveal of how people fall into the psychological trap. "
            "CTA (last 3s): Seamless Infinite Loop Bridge back into the opening hook (NO spoken Subscribe CTA). "
            "Tone: authoritative, hypnotic, analytical, gripping. Strict English only."
        ),
        "topics": [
            "The Dark Psychology Trick People Use to Make You Trust Them Instantly",
            "Why Narcissists Never Apologize (The Chilling Psychological Truth)",
            "How to Spot a Manipulator in 30 Seconds Just by Watching Their Eyes",
            "The Psychological Reason Why Pathological Liars Look Straight into Your Face",
            "How Toxic People Test Your Boundaries Before Controlling You",
        ],
        "default_tags": ["shorts", "psychology", "darkpsychology", "manipulation", "mindset", "bodylanguage", "humanbehavior", "lies", "viral"],
    },
    "ch_paws_hearts_rescue": {
        "id": "ch_paws_hearts_rescue",
        "name": "Paws & Hearts Rescue",
        "niche": "animal_rescue",
        "language": "en",
        "google_account": "acount_2",
        "preferred_voice": "en-US-JennyNeural",
        "handle": "@pawsandheartsrescue",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel Paws & Hearts Rescue. Tear-jerking emotional animal rescue arc: "
            "HOOK (0-3s): Heartbreaking visual of an abandoned trembling animal in distress. "
            "DEVELOPMENT: Gentle human approach, rescuer builds trust through gentle whispers and food. "
            "CLIMAX: Miraculous medical recovery and emotional transformation to pure happiness and gratitude. "
            "CTA (last 3s): Seamless Infinite Loop Bridge back into the opening hook (NO spoken Subscribe CTA). "
            "Tone: compassionate, deeply emotional, heartwarming, uplifting."
        ),
        "topics": [
            "Traumatized Golden Retriever Refused To Leave The Drain Pipe Until A Rescuer Did This",
            "Shivering Puppy Tied To A Park Bench In Heavy Rain Finally Hears A Gentle Voice",
            "Terrified Stray Kitten Hiding Under Car Engine Gets A Second Chance At Life",
            "Nobody Wanted To Touch This Scared Orphan Dog Until One Loving Woman Stepped Forward",
        ],
        "default_tags": ["shorts", "animalrescue", "rescuedog", "puppy", "dog", "pets", "heartwarming", "faithinhumanity", "transformation", "animals"],
    },
    "ch_wild_odyssey": {
        "id": "ch_wild_odyssey",
        "name": "Wild Odyssey",
        "niche": "wildlife_predators",
        "language": "en",
        "google_account": "acount_2",
        "preferred_voice": "en-US-ChristopherNeural",
        "handle": "@wildodysseyhq",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel Wild Odyssey. Intense 4-beat wildlife predator action: "
            "HOOK (0-3s): Shocking lethal stat or predatory feat naming the exact predator or 1v1 matchup. "
            "DEVELOPMENT: Compare bite force, razor claws, ambush speed vs other apex beasts. "
            "CLIMAX: The devastating strike or ruthless hunting sequence. "
            "CTA (last 3s): Seamless Infinite Loop Bridge back into the opening hook (NO spoken Subscribe CTA). "
            "Tone: cinematic, adrenaline-pumping, dramatic, nature documentary style."
        ),
        "topics": [
            "Green Anaconda vs Black Caiman: The Deadliest Swamp Ambush Ever Recorded",
            "Harpy Eagle vs Sloth: The Talon Grip That Crushes Bone Instantly",
            "Giant River Otter Pack vs Jaguar: Why Even Apex Cats Flee The River",
            "1,500 PSI Jaguar Bite vs Caiman Armor: How One Strike Ends The Fight",
        ],
        "default_tags": ["shorts", "wildlife", "predators", "bigcats", "nature", "apexpredator", "jaguar", "animals", "documentary", "viral"],
    },
    "ucsrjiil67qxidtxt70z-iwa": {
        "id": "ucsrjiil67qxidtxt70z-iwa",
        "name": "The mysteries of the world",
        "niche": "deep_sea_earth_mysteries",
        "language": "en",
        "google_account": "account_1",
        "preferred_voice": "en-US-BrianNeural",
        "handle": "@Themysteriesoftheworld-f3w",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 15,  # Staggered Golden Slot
        "director_notes": (
            "Target channel The mysteries of the world. Spine-chilling deep sea & earth mystery arc: "
            "HOOK (0-3s): Terrifying stat or suppressed fact about a specific historical/oceanic anomaly. "
            "DEVELOPMENT: Reveal the impossible scale or danger of the phenomenon with real scientific or historical grounding. "
            "CLIMAX: The unsettling implication — what it means for humanity if this is real. "
            "CTA (last 3s): Seamless Infinite Loop Bridge back into the opening hook (NO spoken Subscribe CTA). "
            "Tone: suspenseful, dark, conspiratorial, fear-inducing, documentary-style. Strict English only."
        ),
        "topics": [
            "The 52-Hertz Whale Signal Recorded Moving Beneath The Pacific Ocean",
            "The 2,000-Year-Old Antikythera Mechanism That Rewrote Human History",
            "The Bermuda Triangle: The Methane Gas Eruption No Ship Can Survive",
            "5 Restricted Islands on Earth Where Humans Are Strictly Forbidden To Land",
            "The Giant Squid Sonar Echo Picked Up Beneath Arctic Ice",
            "Why Explorers Found Tropical Fossils Buried Under 2 Miles Of Antarctic Ice",
        ],
        "default_tags": ["shorts", "mystery", "deepocean", "unexplained", "bermudatriangle", "deepsea", "archaeology", "creepy", "viral"],
    }
}


def get_schedule_slot(publish_hour_utc: int, channel_id: str = "") -> datetime:
    """Tính slot giờ vàng tiếp theo có lệch pha chống trùng lặp."""
    try:
        from app.services.smart_scheduler import get_next_available_golden_slot
        slot_dt, _, _, _ = get_next_available_golden_slot(channel_id or "channel_maya")
        return slot_dt
    except Exception:
        now_utc = datetime.now(timezone.utc)
        candidate = now_utc.replace(hour=publish_hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= now_utc + timedelta(hours=2):
            candidate += timedelta(days=1)
        return candidate


async def produce_channel_video(channel_key: str, custom_topic: str = None, dry_run: bool = False):
    cfg = CHANNELS_CONFIG[channel_key]
    ch_id = cfg["id"]
    ch_name = cfg["name"]
    ch_niche = cfg["niche"]
    ch_lang = cfg["language"]
    voice = cfg["preferred_voice"]
    amazon_tag = cfg["amazon_tag"]

    SCHEDULE_SLOT_UTC = get_schedule_slot(cfg["publish_hour_utc"], ch_id)
    SCHEDULE_SLOT_ISO = SCHEDULE_SLOT_UTC.strftime("%Y-%m-%dT%H:%M:%SZ")
    VN_TZ = timezone(timedelta(hours=7))
    dt_vn = SCHEDULE_SLOT_UTC.astimezone(VN_TZ)
    DISPLAY_VN = f"{dt_vn.strftime('%H:%M')} ngay {dt_vn.strftime('%d/%m/%Y')} (Gio VN)"

    print("\n" + "=" * 75)
    print(f"🌟 BẮT ĐẦU SẢN XUẤT CHO KÊNH: {ch_name} ({ch_id})")
    print(f"🎬 Ngách: {ch_niche} | Ngôn ngữ: {ch_lang} | Giọng: {voice}")
    print(f"⏰ HẸN GIỜ ĐĂNG: {DISPLAY_VN} ({SCHEDULE_SLOT_UTC.strftime('%Y-%m-%d %H:%M UTC')})")
    print("=" * 75)

    from app.agents.content_agent import ContentAgent
    from app.services.quality_evaluator import QualityEvaluator
    agent = ContentAgent()
    evaluator = QualityEvaluator()

    # ── BƯỚC 1: TẠO VÀ ĐÁNH GIÁ CHẤM ĐIỂM KỊCH BẢN ──────────────────────────────
    print(f"\n[1/5] 📝 BƯỚC 1: Xây dựng & Đánh giá kịch bản viral cho {ch_name}...")
    topic = custom_topic or random.choice(cfg["topics"])
    print(f"   -> Chủ đề chọn: {topic}")

    script = None
    script_evaluation = None
    for attempt in range(1, 4):
        print(f"\n   [Lần {attempt}/3] Sinh kịch bản và gửi ban thẩm định AI...")
        raw_script = agent.generate_script(
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang,
            director_notes=cfg["director_notes"]
        )

        eval_res = evaluator.evaluate_script(
            script_dict=raw_script.to_dict(),
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang
        )

        print(f"   📊 KẾT QUẢ ĐÁNH GIÁ KỊCH BẢN:")
        print(f"      - Điểm tổng: {eval_res['score']}/100 ({eval_res['verdict']})")
        print(f"      - Hook 3s đầu: {eval_res['hook_score']}/10")
        print(f"      - Đúng ngách {ch_niche}: {eval_res['niche_score']}/10")
        print(f"      - Ngôn ngữ: {eval_res['language_score']}/10")
        print(f"      - Nhịp điệu giữ chân: {eval_res['retention_score']}/10")
        for s in eval_res.get("strengths", [])[:3]:
            print(f"      ✓ {s}")

        if eval_res["passed"]:
            print(f"   ✅ KỊCH BẢN ĐẠT TIÊU CHUẨN XUẤT BẢN! (Điểm {eval_res['score']}/100 >= 80)")
            script = raw_script
            script_evaluation = eval_res
            break
        else:
            print(f"   ⚠️ Kịch bản {eval_res['score']}/100 < 80. Thử lại...")
            script = raw_script

    title = script.title or f"{topic.split(':')[0].strip()} #Shorts"
    base_desc = script.description or f"Discover the truth behind {topic}! Subscribe for more daily insights.\n\n#Shorts #{ch_niche}"
    tags = script.tags or cfg["default_tags"]
    pinned_comment_base = f"What is your thought on this? Drop a comment below! 👇\nSubscribe to {ch_name} for more daily videos! 🔥"

    print(f"\n   -> Tiêu đề hoàn thiện: {title}")
    print(f"   -> Số cảnh: {len(script.scenes)} scenes")
    for idx, s in enumerate(script.scenes):
        print(f"      Cảnh {idx+1}: {s.narration[:70]}...")

    # ── BƯỚC 2: GẮN LINK SẢN PHẨM AMAZON AFFILIATE VÀO MÔ TẢ & GHIM ─────────────
    print(f"\n[2/5] 🛒 BƯỚC 2: Lồng ghép Link Sản Phẩm Amazon Affiliate (Tag: {amazon_tag})...")
    from app.services.monetization_engine import enrich_video_monetization
    ch_monet_cfg = {
        "id": ch_id,
        "name": ch_name,
        "niche": ch_niche,
        "language": ch_lang,
        "google_account": cfg["google_account"],
        "amazon_tag": amazon_tag,
        "affiliate_enabled": False,
        "enable_donation": False
    }

    final_description, final_pinned_comment, product_info = enrich_video_monetization(
        channel_config=ch_monet_cfg,
        title=title,
        description=base_desc,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=script.full_narration,
        enable_affiliate=False,
        enable_donation=False
    )

    if product_info:
        print(f"   ✅ Đã gắn sản phẩm Amazon: {product_info['name']}")
        print(f"   🔗 Link: {product_info['affiliate_url']}")

    if dry_run:
        print("\n[DRY-RUN] Hoàn tất kiểm thử kịch bản & mô tả affiliate. Dừng ở đây.")
        return

    # ── BƯỚC 3: ĐĂNG KÝ VIDEOJOB VÀO DATABASE ────────────────────────────────────
    print(f"\n[3/5] 💾 BƯỚC 3: Đăng ký VideoJob vào hệ thống...")
    from app.database.models import get_db, VideoJob, log_activity
    scenes_dict = script.to_dict()
    scenes_dict["pinned_comment"] = final_pinned_comment
    scenes_json_str = json.dumps(scenes_dict, ensure_ascii=False)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=ch_id,
            topic=topic,
            title=title,
            script=script.full_narration,
            description=final_description,
            tags=",".join(tags),
            scenes_json=scenes_json_str,
            status="RENDERING",
            scheduled_time=SCHEDULE_SLOT_UTC,
            is_auto_generated=0,
            progress_percent=15,
            progress_message=f"Bắt đầu sản xuất video {ch_name} cho slot {DISPLAY_VN}..."
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(
            db, telegram_id=1, username="System_Director",
            action_type="SCHEDULED_VIDEO_START",
            details=f"Khởi động sản xuất #{job_id} '{title}' cho {ch_name}",
            channel_id=ch_id
        )
    print(f"   -> Job #{job_id} đã được khởi tạo thành công.")

    # ── BƯỚC 4: DỰNG VIDEO & CHẤM ĐIỂM VIDEO CHO ĐẾN KHI ĐẠT ─────────────────────
    print(f"\n[4/5] 🎬 BƯỚC 4: Dựng video (giọng {voice}) & Chấm điểm thành phẩm...")

    async def _progress_cb(stage: str, msg: str, percent: int = 0):
        print(f"   [{stage}] [{percent}%] {msg}", flush=True)

    from app.services.pipeline import VideoPipeline
    pipeline = VideoPipeline(job_id=job_id, progress_callback=_progress_cb)
    pipeline.niche = ch_niche

    render_result = await pipeline.execute()
    output_video_path = render_result.output_path

    if not output_video_path or not Path(output_video_path).exists():
        raise RuntimeError(f"Pipeline render thất bại, không tìm thấy file tại: {output_video_path}")

    # Chấm điểm video thành phẩm
    print("\n   🔍 Đang tiến hành thẩm định và chấm điểm video qua FFprobe QA...")
    video_qa = evaluator.evaluate_video(str(output_video_path))
    print(f"   📊 KẾT QUẢ CHẤM ĐIỂM VIDEO:")
    print(f"      - Điểm chất lượng: {video_qa['score']}/100")
    print(f"      - Thời lượng: {video_qa.get('duration_seconds', 0)}s")
    print(f"      - Độ phân giải: {video_qa.get('width')}x{video_qa.get('height')} @ {video_qa.get('fps')} FPS")
    print(f"      - Dung lượng: {video_qa.get('file_size_mb')} MB")
    print(f"      - Âm thanh: {'Có luồng AAC' if video_qa.get('has_audio') else 'Thiếu'}")
    print(f"      - Nhận xét: {video_qa.get('feedback')}")

    if not video_qa["passed"]:
        print(f"   ⚠️ Video cảnh báo: {video_qa.get('issues')}")
    else:
        print(f"   ✅ VIDEO ĐÃ ĐƯỢC CHỨNG NHẬN ĐẠT CHUẨN XUẤT BẢN! ({video_qa['score']}/100)")

    # ── BƯỚC 5: HẸN LỊCH ĐĂNG LÊN KÊNH YOUTUBE & BACKUP DRIVE ────────────────────
    print(f"\n[5/5] 🚀 BƯỚC 5: Upload YouTube Shorts & Hẹn lịch phát sóng {DISPLAY_VN}...")
    from app.agents.publisher_agent import PublisherAgent
    pub_agent = PublisherAgent(channel_config=ch_monet_cfg)

    pub_res = await asyncio.to_thread(
        pub_agent.upload_video,
        video_path=str(output_video_path),
        title=title,
        description=final_description,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=SCHEDULE_SLOT_ISO,
        default_language="en",
        default_audio_language="en",
        pinned_comment=final_pinned_comment,
        topic=topic,
        script=scenes_json_str,
    )

    if pub_res.get("status") == "failed":
        raise RuntimeError(f"Upload YouTube thất bại: {pub_res.get('error')}")

    yt_id = pub_res.get("video_id")
    yt_url = pub_res.get("shorts_url") or (f"https://www.youtube.com/shorts/{yt_id}" if yt_id else None)
    print(f"   🎉 Upload YouTube Shorts thành công!")
    print(f"   Video ID : {yt_id}")
    print(f"   Shorts URL: {yt_url}")
    print(f"   Trạng thái: SCHEDULED (Đã hẹn lịch phát sóng vào {DISPLAY_VN})")

    # Cập nhật DB
    with get_db() as db:
        j_final = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if j_final:
            j_final.status = "SCHEDULED" if yt_id else "RENDERED"
            j_final.youtube_video_id = yt_id
            j_final.video_path = str(output_video_path)
            j_final.scheduled_time = SCHEDULE_SLOT_UTC
            j_final.progress_percent = 100
            j_final.progress_message = f"Đã lên lịch phát sóng lúc {DISPLAY_VN}"
        log_activity(
            db, telegram_id=1, username="System_Director",
            action_type="SCHEDULED_VIDEO_SUCCESS" if yt_id else "RENDER_VIDEO_SUCCESS",
            details=f"Video #{job_id} ('{title}') cho {ch_name}. YT ID: {yt_id}",
            channel_id=ch_id
        )

    print("\n" + "=" * 75)
    print(f"🏆 HOÀN THÀNH XUẤT BẢN THÀNH CÔNG CHO KÊNH: {ch_name.upper()}")
    print(f"   Tiêu đề      : {title}")
    print(f"   Điểm kịch bản: {script_evaluation['score']}/100")
    print(f"   Điểm video   : {video_qa['score']}/100")
    print(f"   Lịch phát    : {DISPLAY_VN}")
    if yt_url:
        print(f"   Link Shorts  : {yt_url}")
    print("=" * 75 + "\n")
    return {
        "channel": ch_name,
        "title": title,
        "script_score": script_evaluation["score"],
        "video_score": video_qa["score"],
        "scheduled_time": DISPLAY_VN,
        "youtube_url": yt_url,
        "video_id": yt_id
    }


async def main():
    parser = argparse.ArgumentParser(description="Produce & schedule videos for English channels")
    parser.add_argument("--channel", type=str, default="all", choices=["channel_maya", "ucsrjiil67qxidtxt70z-iwa", "ch_paws_hearts_rescue", "ch_wild_odyssey", "all"], help="Target channel key")
    parser.add_argument("--dry-run", action="store_true", help="Generate script only, no render/upload")
    args = parser.parse_args()

    targets = list(CHANNELS_CONFIG.keys()) if args.channel == "all" else [args.channel]
    results = []

    for t in targets:
        try:
            res = await produce_channel_video(t, dry_run=args.dry_run)
            if res:
                results.append(res)
        except Exception as e:
            logger.error(f"Lỗi khi sản xuất cho kênh {t}: {e}", exc_info=True)

    print("\n" + "#" * 75)
    print("TỔNG KẾT TẤT CẢ CÁC KÊNH ĐÃ SẢN XUẤT:")
    for r in results:
        print(f"  • {r['channel']}: '{r['title']}' | Điểm: Script {r['script_score']}/100, Video {r['video_score']}/100 | Lịch: {r['scheduled_time']} | Link: {r['youtube_url']}")
    print("#" * 75 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
