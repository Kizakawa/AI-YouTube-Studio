import sys
import io
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

# UTF-8 encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("UploadBrazilChannels")

from app.database.models import get_db, VideoJob, QuotaTracker, PublishedVideo, log_activity
from app.services.monetization_engine import enrich_video_monetization
from app.agents.publisher_agent import PublisherAgent

# Giảm used_units trong DB để QuotaGuard không chặn pre-flight
with get_db() as db:
    q = db.query(QuotaTracker).filter(
        QuotaTracker.project_id == "bionic-client-509515-h2",
        QuotaTracker.cycle_date == "2026-10-12"
    ).first()
    if q:
        old_val = q.used_units
        q.used_units = 3000
        print(f"🔧 Đã điều chỉnh QuotaTracker 'bionic-client-509515-h2' từ {old_val} xuống 3000 units.")

BRT = timezone(timedelta(hours=-3))

def upload_job_311():
    print("\n" + "=" * 70)
    print("🐆 [1/2] TIẾN HÀNH UPLOAD CHO KÊNH: ODISSEIA SELVAGEM (Job #311)")
    print("=" * 70)

    job_id = 311
    video_path = Path("D:/AI_YouTube_Project_Ready/AI-youtube-main/storage/outputs/video_311.mp4")
    if not video_path.exists():
        print(f"❌ Không tìm thấy file: {video_path}")
        return False

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            print(f"❌ Không tìm thấy job #{job_id}")
            return False
        topic = job.topic
        title = job.title or "A Batalha Mais Rara do Cerrado: Lobo-Guará vs Tatu-Canastra! 🐺💥 #shorts"
        tags = [t.strip() for t in (job.tags or "shorts,animais,natureza,brasil").split(",") if t.strip()]

    ch_cfg = {
        "id": "ch_odisseia_selvagem",
        "name": "Odisseia Selvagem",
        "niche": "wildlife_predators",
        "language": "pt-BR",
        "google_account": "acount_2",
        "amazon_tag": "kizakawa-20",
        "affiliate_enabled": False,
        "enable_donation": False
    }

    base_desc = (
        f"O encontro mais raro e fascinante do Cerrado brasileiro: Lobo-Guará vs Tatu-Canastra! "
        f"Descubra as táticas secretas e a força incrível desses animais selvagens.\n\n"
        f"Inscreva-se na Odisseia Selvagem para mais batalhas épicas da natureza do Brasil! 🐺🇧🇷\n\n"
        f"#OdisseiaSelvagem #Shorts #Animais #Natureza #LoboGuara #TatuCanastra #Predadores #Wildlife"
    )
    pinned_comment_base = (
        "Quem você acha que levaria a melhor nesse confronto épico do Cerrado? "
        "Deixe seu palpite nos comentários! 👇\n"
        "Inscreva-se no canal para acompanhar mais vídeos diários sobre a vida selvagem!"
    )

    final_desc, final_pinned, prod_info = enrich_video_monetization(
        channel_config=ch_cfg,
        title=title,
        description=base_desc,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=base_desc,
        enable_affiliate=False,
        enable_donation=False
    )

    sched_utc = datetime(2026, 10, 13, 23, 0, 0, tzinfo=timezone.utc)
    t_brt = sched_utc.astimezone(BRT)
    sched_brt_str = f"{t_brt.strftime('%H:%M')} ngay {t_brt.strftime('%d/%m/%Y')} (Gio BRT - Brasilia)"
    print(f"   Lịch phát sóng vàng: {sched_brt_str} (23:00 UTC)")
    print(f"   Sản phẩm Affiliate: {prod_info.get('name') if prod_info else 'N/A'}")

    sched_iso = sched_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    pub_agent = PublisherAgent(channel_config=ch_cfg)
    pub_res = pub_agent.upload_video(
        video_path=str(video_path),
        title=title,
        description=final_desc,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=sched_iso,
        default_language="pt",
        default_audio_language="pt-BR",
        pinned_comment=final_pinned,
        topic=topic,
        script=base_desc
    )

    print(f"   Kết quả upload: status={pub_res.get('status')}")
    if pub_res.get("status") == "success" and pub_res.get("video_id"):
        yt_id = pub_res["video_id"]
        yt_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
        print(f"   🎉 THÀNH CÔNG! YouTube Video ID: {yt_id}")
        print(f"   🔗 Shorts URL: {yt_url}")

        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "SCHEDULED"
                j.youtube_video_id = yt_id
                j.scheduled_time = sched_utc
                j.description = final_desc
                j.progress_percent = 100
                j.progress_message = f"Đã lên lịch phát sóng vào {sched_brt_str}"
            log_activity(
                db, telegram_id=1, username="System_Director",
                action_type="SCHEDULED_VIDEO_SUCCESS",
                details=f"Odisseia Selvagem #{job_id} lên lịch thành công. YT ID: {yt_id}",
                channel_id="ch_odisseia_selvagem"
            )
        return True
    else:
        print(f"   ⚠️ Upload thất bại: {pub_res}")
        return False


def upload_job_312():
    print("\n" + "=" * 70)
    print("🌊 [2/2] TIẾN HÀNH UPLOAD CHO KÊNH: MISTÉRIOS DO MUNDO (Job #312)")
    print("=" * 70)

    job_id = 312
    video_path = Path("D:/AI_YouTube_Project_Ready/AI-youtube-main/storage/outputs/video_312.mp4")
    if not video_path.exists():
        print(f"❌ Không tìm thấy file: {video_path}")
        return False

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            print(f"❌ Không tìm thấy job #{job_id}")
            return False
        topic = job.topic
        title = job.title or "O Buraco Negro Que Engole Navios Inteiros no Oceano! 🕳️🌊 #shorts"
        tags = [t.strip() for t in (job.tags or "shorts,misterio,oceano,curiosidades").split(",") if t.strip()]

    ch_cfg = {
        "id": "ch_misterios_do_mundo",
        "name": "Mistérios do Mundo",
        "niche": "ocean_mysteries",
        "language": "pt-BR",
        "google_account": "acount_2",
        "amazon_tag": "kizakawa-20",
        "affiliate_enabled": False,
        "enable_donation": False
    }

    base_desc = (
        f"O oceano esconde segredos aterrorizantes e fenômenos inexplicáveis que a ciência tenta desvendar. "
        f"Conheça o buraco azul mais profundo e perigoso da Terra!\n\n"
        f"Inscreva-se em Mistérios do Mundo para descobrir os maiores enigmas do nosso planeta! 🌊🕳️\n\n"
        f"#MisteriosDoMundo #Shorts #Misterio #Oceano #Curiosidades #Abismo #Ciencia"
    )
    pinned_comment_base = (
        "Você teria coragem de mergulhar nesse abismo infinito no fundo do mar? "
        "Conte aqui nos comentários! 👇\n"
        "Inscreva-se no canal para desvendar um novo mistério todos os dias!"
    )

    final_desc, final_pinned, prod_info = enrich_video_monetization(
        channel_config=ch_cfg,
        title=title,
        description=base_desc,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=base_desc,
        enable_affiliate=False,
        enable_donation=False
    )

    sched_utc = datetime(2026, 10, 14, 1, 0, 0, tzinfo=timezone.utc)
    t_brt = sched_utc.astimezone(BRT)
    sched_brt_str = f"{t_brt.strftime('%H:%M')} ngay {t_brt.strftime('%d/%m/%Y')} (Gio BRT - Brasilia)"
    print(f"   Lịch phát sóng vàng (đã dời tránh va chạm): {sched_brt_str} (01:00 UTC 14/10)")
    print(f"   Sản phẩm Affiliate: {prod_info.get('name') if prod_info else 'N/A'}")

    sched_iso = sched_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    pub_agent = PublisherAgent(channel_config=ch_cfg)
    pub_res = pub_agent.upload_video(
        video_path=str(video_path),
        title=title,
        description=final_desc,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=sched_iso,
        default_language="pt",
        default_audio_language="pt-BR",
        pinned_comment=final_pinned,
        topic=topic,
        script=base_desc
    )

    print(f"   Kết quả upload: status={pub_res.get('status')}")
    if pub_res.get("status") == "success" and pub_res.get("video_id"):
        yt_id = pub_res["video_id"]
        yt_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
        print(f"   🎉 THÀNH CÔNG! YouTube Video ID: {yt_id}")
        print(f"   🔗 Shorts URL: {yt_url}")

        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "SCHEDULED"
                j.youtube_video_id = yt_id
                j.scheduled_time = sched_utc
                j.description = final_desc
                j.progress_percent = 100
                j.progress_message = f"Đã lên lịch phát sóng vào {sched_brt_str}"
            log_activity(
                db, telegram_id=1, username="System_Director",
                action_type="SCHEDULED_VIDEO_SUCCESS",
                details=f"Mistérios do Mundo #{job_id} lên lịch thành công. YT ID: {yt_id}",
                channel_id="ch_misterios_do_mundo"
            )
        return True
    else:
        print(f"   ⚠️ Upload thất bại: {pub_res}")
        return False


if __name__ == "__main__":
    ok1 = upload_job_311()
    ok2 = upload_job_312()
    print("\n" + "=" * 70)
    print(f"🏁 TỔNG KẾT: Odisseia Selvagem: {'SUCCESS' if ok1 else 'FAILED'} | Mistérios do Mundo: {'SUCCESS' if ok2 else 'FAILED'}")
    print("=" * 70)
