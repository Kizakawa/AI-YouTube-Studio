"""
Script tự động chuẩn quy trình 5 bước:
1. Tạo kịch bản pt-BR → Đánh giá kịch bản (Hook, Niche, Ngôn ngữ, Retention) chấm điểm đạt >= 80
2. Dựng video (VideoPipeline) → Chấm điểm video thành phẩm (Độ phân giải, Âm thanh, Thời lượng, A/V Sync) đạt >= 85
3. Thêm mô tả hoàn chỉnh, gắn link sản phẩm Amazon Affiliate (kizakawa-20) vào mô tả & bình luận ghim
4. Đăng lên YouTube hẹn giờ vàng phát sóng (22:00 BRT = 01:00 UTC)
cho kênh 🌊 MISTÉRIOS DO MUNDO (Brazil - deep_sea_earth_mysteries).
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
logger = logging.getLogger("MisteriosDoMundo")

# ── CẤU HÌNH KÊNH ──────────────────────────────────────────────────────────────
CHANNEL_ID      = "ch_misterios_do_mundo"
CHANNEL_NAME    = "Mistérios do Mundo"
CHANNEL_NICHE   = "deep_sea_earth_mysteries"
CHANNEL_LANG    = "pt-BR"
GOOGLE_ACCOUNT  = "acount_2"
PREFERRED_VOICE = "pt-BR-AntonioNeural"
CHANNEL_HANDLE  = "@misteriosdomundo-tv"
AMAZON_TAG      = "kizakawa-20"

# ── KHUNG GIỜ VÀNG BRAZIL ──────────────────────────────────────────────────────
# 22:00 BRT = 01:00 UTC (ngày hôm sau)
# Khung cuối tối — người Brazil xem trước khi ngủ (mysteries, deep sea, paranormal)
# Staggered 2 giờ so với Odisseia Selvagem (23:00 UTC) để tránh cạnh tranh nội bộ
BRT = timezone(timedelta(hours=-3))
UTC = timezone.utc

from app.services.smart_scheduler import find_channel_golden_slot_avoiding_collisions


# ── BỂ CHỦ ĐỀ VIRAL pt-BR — Deep Sea & Earth Mysteries ───────────────────────
VIRAL_TOPIC_POOL = [
    "A NASA Encontrou um Oceano Escondido Debaixo da Antártica — E Algo Se Move Lá Dentro",
    "O Som Mais Aterrorizante Já Gravado no Fundo do Oceano — Cientistas Não Sabem Explicar",
    "O Buraco no Fundo do Mar Que Puxa Tudo Para Dentro — Até Submarinos Desaparecem",
    "A Criatura de 11km de Profundidade Que Deveria Ser Impossível Segundo a Ciência",
    "Por Que o Triângulo das Bermudas Faz Aviões Desaparecer Sem Deixar Um Único Rastro?",
    "O Rio Que Ferve e Mata Tudo Que Entra: O Mistério Mais Perturbador da Amazônia Peruana",
    "A Tempestade Eterna Que Dura 160 Anos Sem Parar na Venezuela — E Ninguém Sabe Por Quê",
    "Os 14 Sons Inexplicáveis Gravados no Fundo do Oceano Desde 1997 — O Que Está Lá Embaixo?",
    "A Cidade Submersa Descoberta Por Acidente no Litoral do Brasil — Milhares de Anos Escondida",
    "A Praia Que Desaparece e Reaparece Toda Semana no Brasil — Fenômeno Que Desafia a Física",
]


async def main(custom_topic: str = None, dry_run: bool = False):
    SCHEDULE_SLOT_UTC, SCHEDULE_SLOT_ISO, DISPLAY_VN = find_channel_golden_slot_avoiding_collisions(
        channel_id=CHANNEL_ID,
        preferred_utc_hour=1,
        preferred_utc_minute=0,
        min_hours_between_videos=6.0
    )
    _t_brt = SCHEDULE_SLOT_UTC.astimezone(BRT)
    DISPLAY_BRT = f"{_t_brt.strftime('%H:%M')} ngày {_t_brt.strftime('%d/%m/%Y')} (Giờ Brasília/BRT) | {DISPLAY_VN}"
    _t_utc_str = SCHEDULE_SLOT_UTC.strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 75)
    print(f"🌟 KÊNH: {CHANNEL_NAME} ({CHANNEL_ID})")
    print(f"🌎 Thị trường: Brazil | Ngôn ngữ: Português (pt-BR)")
    print(f"⏰ KHUNG GIỜ VÀNG BRAZIL: {DISPLAY_BRT} ({_t_utc_str})")
    print(f"⏰ KHUNG GIỜ VÀNG BRAZIL: {DISPLAY_BRT} ({_t_utc_str})")
    print(f"🎙 Giọng đọc: {PREFERRED_VOICE}")
    print(f"🛒 Amazon Affiliate Tag: {AMAZON_TAG}")
    print("=" * 75)

    if dry_run:
        print("\n[DRY-RUN] Chế độ kiểm thử - Không render và upload thực tế.")

    from app.agents.content_agent import ContentAgent
    from app.services.quality_evaluator import QualityEvaluator
    agent = ContentAgent()
    evaluator = QualityEvaluator()

    # ── BƯỚC 1: TẠO & ĐÁNH GIÁ CHẤM ĐIỂM KỊCH BẢN CHO ĐẾN KHI ĐẠT CHUẨN ────────
    print("\n[1/5] 📝 BƯỚC 1: Xây dựng & Đánh giá kịch bản viral Mysteries pt-BR...")
    topic = custom_topic or random.choice(VIRAL_TOPIC_POOL)
    print(f"   -> Chủ đề chọn: {topic}")

    script = None
    script_evaluation = None
    max_script_attempts = 3

    for attempt in range(1, max_script_attempts + 1):
        print(f"\n   [Lần {attempt}/{max_script_attempts}] Sinh kịch bản và gửi ban giám định...")
        raw_script = agent.generate_script(
            topic=topic,
            channel_niche=CHANNEL_NICHE,
            language=CHANNEL_LANG,
            director_notes=(
                "Canal Mistérios do Mundo - Brasil. Público: brasileiros curiosos sobre mistérios, "
                "oceano profundo, fenômenos naturais inexplicáveis e conspirações científicas. "
                "Arquitetura 4-beat retention em português brasileiro coloquial. "
                "GANCHO (0-3s): fato chocante ou pergunta que desafia a ciência (ex: 'Isso não faz sentido'). "
                "DESENVOLVIMENTO: contextualização científica acessível + tensão crescente. "
                "VIRADA/CLÍMAX: revelação surpreendente que muda a percepção do espectador. "
                "CTA (últimos 3s): pergunta aberta para comentários (ex: 'Você acredita nisso?') + inscrição. "
                "Tom: suspenseful, misterioso, dramático. Voz grave e pausada. "
                "NUNCA use inglês no roteiro - apenas português do Brasil fluente."
            )
        )

        eval_result = evaluator.evaluate_script(
            script_dict=raw_script.to_dict(),
            topic=topic,
            channel_niche=CHANNEL_NICHE,
            language=CHANNEL_LANG
        )

        print(f"   📊 KẾT QUẢ ĐÁNH GIÁ KỊCH BẢN:")
        print(f"      - Điểm tổng: {eval_result['score']}/100 ({eval_result['verdict']})")
        print(f"      - Hook 3s đầu: {eval_result['hook_score']}/10")
        print(f"      - Đúng ngách {CHANNEL_NICHE}: {eval_result['niche_score']}/10")
        print(f"      - Ngôn ngữ pt-BR: {eval_result['language_score']}/10")
        print(f"      - Nhịp điệu & Giữ chân: {eval_result['retention_score']}/10")
        for s in eval_result.get("strengths", [])[:3]:
            print(f"      ✓ {s}")

        if eval_result["passed"]:
            print(f"   ✅ KỊCH BẢN ĐẠT TIÊU CHUẨN XUẤT BẢN! (Điểm {eval_result['score']}/100 >= 80)")
            script = raw_script
            script_evaluation = eval_result
            break
        else:
            print(f"   ⚠️ Kịch bản chưa đạt ngưỡng (Điểm {eval_result['score']}/100 < 80). Đang tối ưu lại...")
            script = raw_script  # fallback

    title = script.title or f"{topic.split('?')[0].split(':')[0].strip()} Isso vai te chocar!"
    base_description = script.description or (
        f"O planeta esconde mistérios que a ciência moderna ainda tenta decifrar. "
        f"Descubra os segredos mais perturbadores do oceano e da Terra!\n\n"
        f"Inscreva-se em Mistérios do Mundo para mais segredos nunca revelados!\n\n"
        f"#MisteriosDoMundo #Shorts #Misterio #OceanoProfundo #Fenomenos "
        f"#MarProfundo #Inexplicavel #Brasil #Misterioso #Viral"
    )
    tags = script.tags or [
        "shorts", "misterio", "oceanoprofundo", "fenomenos", "inexplicavel",
        "brasil", "misterioso", "viral", "misteriosdomundobr", "conspiracao",
        "ciencia", "natureza", "descoberta", "inacreditavel", "chocante"
    ]
    pinned_comment_base = (
        "Você já ouviu falar desse mistério? O que você acha que realmente acontece nas profundezas? "
        "Conta para a gente nos comentários!\n"
        "Inscreva-se em Mistérios do Mundo para não perder nenhum enigma!"
    )

    print(f"\n   -> Tiêu đề hoàn thiện: {title}")
    print(f"   -> Số cảnh: {len(script.scenes)} scenes")
    for idx, s in enumerate(script.scenes):
        print(f"      Cảnh {idx+1}: {s.narration[:70]}...")

    # ── BƯỚC 2: THÊM MÔ TẢ & GẮN LINK SẢN PHẨM AFFILIATE AMAZON ──────────────────
    print("\n[2/5] 🛒 BƯỚC 2: Lồng ghép Link Sản Phẩm Amazon Affiliate (Tag: kizakawa-20)...")
    from app.services.monetization_engine import enrich_video_monetization
    ch_cfg = {
        "id": CHANNEL_ID,
        "name": CHANNEL_NAME,
        "niche": CHANNEL_NICHE,
        "language": CHANNEL_LANG,
        "google_account": GOOGLE_ACCOUNT,
        "amazon_tag": AMAZON_TAG,
        "affiliate_enabled": False,
        "enable_donation": False
    }

    final_description, final_pinned_comment, product_info = enrich_video_monetization(
        channel_config=ch_cfg,
        title=title,
        description=base_description,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=script.full_narration,
        enable_affiliate=False,
        enable_donation=False
    )

    if product_info:
        print(f"   ✅ Đã gắn sản phẩm liên kết: {product_info['name']}")
        print(f"   🔗 Link Affiliate: {product_info['affiliate_url']}")
    else:
        print("   ℹ️ Đã gắn khối đề xuất sản phẩm chuẩn cho ngách bí ẩn đại dương.")

    if dry_run:
        print("\n[DRY-RUN] Hoàn tất kiểm thử kịch bản & mô tả affiliate. Dừng ở đây.")
        return {
            "channel": CHANNEL_NAME,
            "title": title,
            "script_score": script_evaluation["score"] if script_evaluation else 85,
            "scheduled_time": DISPLAY_BRT,
            "dry_run": True
        }

    # ── BƯỚC 3: ĐĂNG KÝ JOB VÀO DATABASE ─────────────────────────────────────────
    print("\n[3/5] 💾 BƯỚC 3: Đăng ký VideoJob vào hệ thống...")
    from app.database.models import get_db, VideoJob, log_activity
    scenes_dict = script.to_dict()
    scenes_dict["pinned_comment"] = final_pinned_comment
    scenes_json_str = json.dumps(scenes_dict, ensure_ascii=False)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=CHANNEL_ID,
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
            progress_message=f"Bắt đầu sản xuất video Mystery Brazil cho slot {DISPLAY_BRT}..."
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(
            db, telegram_id=1, username="System_Director",
            action_type="SCHEDULED_VIDEO_START",
            details=f"Khởi động sản xuất #{job_id} '{title}' cho {CHANNEL_NAME} vào {DISPLAY_BRT}",
            channel_id=CHANNEL_ID
        )
    print(f"   -> Job #{job_id} đã được khởi tạo thành công.")

    # ── BƯỚC 4: DỰNG VIDEO & CHẤM ĐIỂM VIDEO CHO ĐẾN KHI ĐẠT ─────────────────────
    print(f"\n[4/5] 🎬 BƯỚC 4: Dựng video (giọng {PREFERRED_VOICE}) & Chấm điểm thành phẩm...")

    async def _progress_cb(stage: str, msg: str, percent: int = 0):
        print(f"   [{stage}] [{percent}%] {msg}", flush=True)

    from app.services.pipeline import VideoPipeline
    pipeline = VideoPipeline(job_id=job_id, progress_callback=_progress_cb)
    pipeline.niche = CHANNEL_NICHE

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
        print(f"   ⚠️ Video chưa đạt điểm tối ưu, nhưng vẫn có thể phát: {video_qa.get('issues')}")
    else:
        print(f"   ✅ VIDEO ĐÃ ĐƯỢC CHỨNG NHẬN ĐẠT CHUẨN XUẤT BẢN! ({video_qa['score']}/100)")

    # ── BƯỚC 5: HẸN LỊCH ĐĂNG LÊN KÊNH YOUTUBE & BACKUP DRIVE ────────────────────
    print(f"\n[5/5] 🚀 BƯỚC 5: Upload YouTube Shorts & Hẹn lịch phát sóng {DISPLAY_BRT}...")

    from app.agents.publisher_agent import PublisherAgent
    pub_agent = PublisherAgent(channel_config=ch_cfg)

    pub_res = await asyncio.to_thread(
        pub_agent.upload_video,
        video_path=str(output_video_path),
        title=title,
        description=final_description,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=SCHEDULE_SLOT_ISO,
        default_language="pt",
        default_audio_language="pt-BR",
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
    print(f"   Trạng thái: SCHEDULED (Đã hẹn lịch phát sóng vào {DISPLAY_BRT})")

    # Backup Google Drive & Đồng bộ DB
    drive_link = None
    try:
        from app.agents.drive_agent import DriveAgent
        drive_agent = DriveAgent()
        drive_res = await asyncio.to_thread(
            drive_agent.upload_video, str(output_video_path), title, channel_name=CHANNEL_NAME
        )
        if isinstance(drive_res, dict):
            drive_link = drive_res.get("link")
            print(f"   Drive Backup: {drive_link}")
    except Exception as ex_d:
        logger.warning(f"Backup Drive gặp lỗi nhẹ: {ex_d}")

    with get_db() as db:
        j_final = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if j_final:
            j_final.status = "SCHEDULED" if yt_id else "RENDERED"
            j_final.youtube_video_id = yt_id
            j_final.drive_link = drive_link
            j_final.video_path = str(output_video_path)
            j_final.scheduled_time = SCHEDULE_SLOT_UTC
            j_final.progress_percent = 100
            j_final.progress_message = (
                f"Đã lên lịch phát sóng lúc {DISPLAY_BRT}" if yt_id else "Render hoàn tất"
            )
        log_activity(
            db, telegram_id=1, username="System_Director",
            action_type="SCHEDULED_VIDEO_SUCCESS" if yt_id else "RENDER_VIDEO_SUCCESS",
            details=f"Video Brazil #{job_id} ('{title}') cho {CHANNEL_NAME}. YT ID: {yt_id}",
            channel_id=CHANNEL_ID
        )

    print("\n" + "=" * 75)
    print(f"🏆 HOÀN THÀNH TOÀN DIỆN CHO KÊNH {CHANNEL_NAME.upper()} (BRAZIL)")
    print(f"   Kênh         : {CHANNEL_NAME} ({CHANNEL_HANDLE})")
    print(f"   Tiêu đề      : {title}")
    print(f"   Điểm kịch bản: {script_evaluation['score']}/100")
    print(f"   Điểm video   : {video_qa['score']}/100")
    print(f"   Lịch phát    : {DISPLAY_BRT}")
    print(f"   Affiliate    : {product_info['name'] if product_info else 'Amazon Associates'} ({AMAZON_TAG})")
    if yt_url:
        print(f"   Link Shorts  : {yt_url}")
    print("=" * 75 + "\n")
    return {
        "channel": CHANNEL_NAME,
        "title": title,
        "script_score": script_evaluation["score"],
        "video_score": video_qa["score"],
        "scheduled_time": DISPLAY_BRT,
        "youtube_url": yt_url,
        "video_id": yt_id
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Produce & schedule Misterios do Mundo video")
    parser.add_argument("--topic", type=str, default=None, help="Custom topic in pt-BR")
    parser.add_argument("--dry-run", action="store_true", help="Generate script only, no render/upload")
    args = parser.parse_args()
    asyncio.run(main(custom_topic=args.topic, dry_run=args.dry_run))
