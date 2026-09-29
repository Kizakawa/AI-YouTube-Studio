"""
app.utils.agent_ctl — Công cụ điều khiển CLI trực tiếp dành cho AI Agent.
Cho phép AI Agent tra cứu thống kê, quản lý hàng đợi, đẩy/quét Google Drive và đăng YouTube
ngay từ terminal với tốc độ cao, không cần qua bước xác thực 2FA.

Cách sử dụng:
  py -m app.utils.agent_ctl stats
  py -m app.utils.agent_ctl jobs [--status SCRIPT_READY] [--limit 10]
  py -m app.utils.agent_ctl job <job_id>
  py -m app.utils.agent_ctl create --topic "Chủ đề" [--channel channel_1] [--auto-drive]
  py -m app.utils.agent_ctl push-drive <job_id>
  py -m app.utils.agent_ctl check-drive <job_id>
  py -m app.utils.agent_ctl scan-drive
  py -m app.utils.agent_ctl publish <job_id>
  py -m app.utils.agent_ctl retry <job_id>
  py -m app.utils.agent_ctl cancel <job_id>
  py -m app.utils.agent_ctl channels
  py -m app.utils.agent_ctl logs [--limit 15]
  py -m app.utils.agent_ctl reset-stuck
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Cấu hình encoding UTF-8 cho Windows console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đưa workspace vào sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings
from app.database.models import get_db, VideoJob, ActivityLog, User, log_activity


def cmd_stats(args):
    """Báo cáo thống kê toàn diện hệ thống"""
    with get_db() as db:
        all_jobs = db.query(VideoJob.status).all()
        counts = {}
        for (st,) in all_jobs:
            counts[st] = counts.get(st, 0) + 1

        total = len(all_jobs)
        user = db.query(User).filter(User.telegram_id == 0).first()
        active_ch = user.active_channel_id if user else "channel_1"

    # Drive count
    pending_drive = 0
    completed_drive = 0
    try:
        from app.agents.drive_agent import DriveAgent
        da = DriveAgent()
        pending_drive = len(da.list_pending_tasks())
        completed_drive = len(da.list_completed_videos())
    except Exception as e:
        pending_drive = f"Lỗi ({e})"

    # Colab status
    from app.services.colab_client import colab_client
    is_colab, c_info = colab_client.check_health(timeout=1.0)

    stats = {
        "timestamp": datetime.utcnow().isoformat(),
        "total_jobs": total,
        "status_breakdown": counts,
        "active_channel": active_ch,
        "colab_gpu_online": is_colab,
        "colab_gpu_name": c_info.get("gpu") if is_colab else None,
        "drive_pending_tasks": pending_drive,
        "drive_completed_videos": completed_drive,
        "gemini_keys_count": len(settings.get_gemini_keys_pool()),
        "agent_api_key": settings.AGENT_API_KEY
    }

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print("=" * 65)
        print("🚀 BÁO CÁO THỐNG KÊ HỆ THỐNG AI YOUTUBE STUDIO")
        print("=" * 65)
        print(f"📊 Tổng số VideoJob: {total}")
        print(f"   • SCRIPT_READY : {counts.get('SCRIPT_READY', 0)}")
        print(f"   • PENDING_COLAB: {counts.get('PENDING_COLAB', 0)}")
        print(f"   • RENDERING    : {counts.get('RENDERING', 0)}")
        print(f"   • RENDERED     : {counts.get('RENDERED', 0)}")
        print(f"   • PUBLISHED    : {counts.get('PUBLISHED', 0)}")
        print(f"   • FAILED       : {counts.get('FAILED', 0)}")
        print(f"   • DRAFT        : {counts.get('DRAFT', 0)}")
        print("-" * 65)
        print(f"☁️ Google Drive Farm: {pending_drive} task chờ | {completed_drive} video xong")
        print(f"⚡ Colab GPU Farm   : {'🟢 ONLINE (' + str(c_info.get('gpu')) + ')' if is_colab else '⚪ Offline'}")
        print(f"📺 Kênh hoạt động   : {active_ch}")
        print(f"🔑 Gemini Keys Pool : {len(settings.get_gemini_keys_pool())} keys")
        print(f"🛡️ Agent Secret Key : {settings.AGENT_API_KEY}")
        print("=" * 65)


def cmd_jobs(args):
    """Liệt kê danh sách jobs gần nhất"""
    with get_db() as db:
        q = db.query(VideoJob)
        if args.status:
            q = q.filter(VideoJob.status == args.status.upper())
        if args.channel:
            q = q.filter(VideoJob.target_channel_id == args.channel)

        jobs = q.order_by(VideoJob.id.desc()).limit(args.limit).all()

        if args.json:
            out = [
                {
                    "id": j.id,
                    "title": j.title or j.topic,
                    "topic": j.topic,
                    "status": j.status,
                    "channel": j.target_channel_id,
                    "progress": f"{j.progress_percent}%",
                    "drive_link": j.drive_link,
                    "youtube_id": j.youtube_video_id,
                    "error": j.error_message,
                    "created_at": str(j.created_at)
                }
                for j in jobs
            ]
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"📋 Danh sách {len(jobs)} jobs gần nhất:")
            for j in jobs:
                v_flag = "🎬 [Video OK]" if j.video_path and os.path.exists(j.video_path) else ""
                yt_flag = f"📺 [YT: {j.youtube_video_id}]" if j.youtube_video_id else ""
                print(f"#{j.id:<4} | {j.status:<14} | {j.target_channel_id:<12} | {j.title or j.topic} {v_flag} {yt_flag}")


def cmd_job(args):
    """Xem chi tiết 1 job cụ thể"""
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == args.job_id).first()
        if not j:
            print(f"❌ Không tìm thấy VideoJob #{args.job_id}")
            return

        detail = {
            "id": j.id,
            "title": j.title or j.topic,
            "topic": j.topic,
            "status": j.status,
            "channel_id": j.target_channel_id,
            "progress_percent": j.progress_percent,
            "progress_message": j.progress_message,
            "full_narration": j.script,
            "video_path": j.video_path,
            "video_exists": bool(j.video_path and os.path.exists(j.video_path)),
            "drive_link": j.drive_link,
            "youtube_id": j.youtube_video_id,
            "error_message": j.error_message,
            "created_at": str(j.created_at)
        }
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def cmd_create(args):
    """Tạo mới kịch bản từ Topic"""
    from app.agents.content_agent import ContentAgent
    content_agent = ContentAgent()

    ch_id = args.channel or "channel_1"
    ch_cfg = next((c for c in settings.get_channels_config() if c.get("id") == ch_id), {})
    lang = args.lang or ch_cfg.get("language", "en")

    print(f"⏳ Đang viết kịch bản AI cho topic: '{args.topic}' (kênh: {ch_id}, ngôn ngữ: {lang})...")
    contract = content_agent.create_structured_script(args.topic, target_language=lang)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=ch_id,
            topic=args.topic,
            title=contract.title,
            script=contract.full_narration,
            scenes_json=contract.to_json_str(),
            status="SCRIPT_READY",
            progress_percent=20,
            progress_message="Kịch bản AI sẵn sàng"
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(db, 0, "Agent CLI", "AGENT_CLI_CREATE", f"Tạo VideoJob #{job_id}: {contract.title}")

    print(f"✅ Đã tạo thành công VideoJob #{job_id}: '{contract.title}'")

    if args.auto_drive:
        from app.services.colab_queue_service import push_job_to_drive_queue
        ok, msg, d_link = push_job_to_drive_queue(job_id)
        if ok:
            print(f"🚀 Đã tự động đẩy lên Google Drive: {d_link}")
        else:
            print(f"⚠️ Lỗi đẩy Drive: {msg}")


def cmd_push_drive(args):
    """Đẩy task kịch bản vào hàng đợi Google Drive"""
    from app.services.colab_queue_service import push_job_to_drive_queue
    ok, msg, d_link = push_job_to_drive_queue(args.job_id)
    if ok:
        print(f"✅ THÀNH CÔNG: Đã đẩy VideoJob #{args.job_id} vào Drive pending: {d_link}")
    else:
        print(f"❌ THẤT BẠI: {msg}")


def cmd_check_drive(args):
    """Kiểm tra và tải video đã xong từ Google Drive"""
    from app.services.colab_queue_service import check_and_import_completed_video
    ok, local_p, d_link = check_and_import_completed_video(args.job_id)
    if ok:
        print(f"🎉 THÀNH CÔNG: Video #{args.job_id} đã tải về local: {local_p}")
        print(f"☁️ Link Drive: {d_link}")
    else:
        print(f"ℹ️ {local_p}")


def cmd_scan_drive(args):
    """Quét và nhập tất cả video đã render xong từ Drive"""
    with get_db() as db:
        jobs = db.query(VideoJob).filter(VideoJob.status.in_(["PENDING_COLAB", "RENDERING", "SCRIPT_READY"])).all()
        ids = [j.id for j in jobs]

    print(f"🔍 Đang quét Drive cho {len(ids)} jobs đang chờ: {ids}...")
    from app.services.colab_queue_service import check_and_import_completed_video
    imported = 0
    for j_id in ids:
        ok, local_p, d_link = check_and_import_completed_video(j_id)
        if ok:
            print(f"  ✅ Đã nhập Video #{j_id}: {local_p}")
            imported += 1
    print(f"✨ Hoàn tất quét Drive: Đã nhập {imported}/{len(ids)} video.")


def cmd_publish(args):
    """Đăng video lên YouTube Shorts ngay lập tức"""
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == args.job_id).first()
        if not j:
            print(f"❌ Không tìm thấy job #{args.job_id}")
            return
        if not j.video_path or not os.path.exists(j.video_path):
            print(f"❌ File video không tồn tại: {j.video_path}")
            return

        ch_id = j.target_channel_id
        ch_cfg = next((c for c in settings.get_channels_config() if c.get("id") == ch_id), {})
        v_title = j.title or j.topic
        v_desc = j.description or f"{v_title}\n\n#Shorts #viral"
        v_tags = j.tags or "shorts, viral"
        v_path = j.video_path

    print(f"🚀 Đang đăng Video #{args.job_id} lên YouTube Shorts (Kênh: {ch_cfg.get('name', ch_id)})...")
    from app.agents.publisher_agent import PublisherAgent
    pub_agent = PublisherAgent(channel_config=ch_cfg)
    res = pub_agent.upload_video(
        video_path=v_path,
        title=v_title,
        description=v_desc,
        tags=v_tags,
        privacy_status="public",
        default_language=ch_cfg.get("language", "en"),
        default_audio_language=ch_cfg.get("language", "en")
    )

    if res.get("status") == "success":
        yt_id = res.get("video_id")
        yt_url = res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == args.job_id).first()
            if j:
                j.status = "PUBLISHED"
                j.youtube_video_id = yt_id
                log_activity(db, 0, "Agent CLI", "PUBLISH_YOUTUBE", f"Đăng Video #{args.job_id} lên YouTube: {yt_url}")
        print(f"🎉 ĐĂNG THÀNH CÔNG: {yt_url}")
    else:
        print(f"❌ LỖI ĐĂNG YOUTUBE: {res.get('error')}")


def cmd_retry(args):
    """Đặt lại trạng thái kịch bản về SCRIPT_READY"""
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == args.job_id).first()
        if not j:
            print(f"❌ Không tìm thấy job #{args.job_id}")
            return
        j.status = "SCRIPT_READY"
        j.error_message = None
        j.progress_percent = 20
        j.progress_message = "Đặt lại trạng thái qua Agent CLI"
        log_activity(db, 0, "Agent CLI", "RETRY_JOB", f"Reset Video #{args.job_id}")
    print(f"✅ Đã đặt lại Video #{args.job_id} về SCRIPT_READY.")


def cmd_cancel(args):
    """Hủy 1 VideoJob"""
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == args.job_id).first()
        if not j:
            print(f"❌ Không tìm thấy job #{args.job_id}")
            return
        j.status = "FAILED"
        j.error_message = "Hủy bởi Agent CLI"
        log_activity(db, 0, "Agent CLI", "CANCEL_JOB", f"Hủy Video #{args.job_id}")
    print(f"✅ Đã hủy Video #{args.job_id}.")


def cmd_channels(args):
    """Xem danh sách các kênh cấu hình"""
    cfg = settings.get_channels_config()
    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 0).first()
        active = u.active_channel_id if u else "channel_1"

    print("📺 DANH SÁCH KÊNH YOUTUBE ĐÃ CẤU HÌNH:")
    for ch in cfg:
        is_act = " [ĐANG CHỌN ✓]" if ch.get("id") == active else ""
        print(f"  • {ch.get('id'):<12} | {ch.get('name'):<25} | Niche: {ch.get('niche'):<15} | Lang: {ch.get('language')}{is_act}")


def cmd_logs(args):
    """Xem nhật ký hành động gần nhất"""
    with get_db() as db:
        logs = db.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(args.limit).all()
        print(f"📜 {len(logs)} Nhật ký hành động gần nhất:")
        for l in logs:
            print(f"[{l.created_at.strftime('%m-%d %H:%M:%S')}] {l.action_type:<20} | {l.username:<12} | {l.details}")


def cmd_reset_stuck(args):
    """Reset các job bị kẹt ở RENDERING"""
    cutoff = datetime.utcnow() - timedelta(minutes=args.minutes)
    with get_db() as db:
        stuck = db.query(VideoJob).filter(
            VideoJob.status.in_(["RENDERING", "PENDING_COLAB"]),
            VideoJob.updated_at < cutoff
        ).all()
        ids = [j.id for j in stuck]
        for j in stuck:
            j.status = "SCRIPT_READY"
            j.progress_message = f"Tự động reset từ {j.status} do treo > {args.minutes} phút"
    print(f"✨ Đã khôi phục {len(ids)} jobs bị treo về SCRIPT_READY: {ids}")


def main():
    parser = argparse.ArgumentParser(description="AI Agent Super-Control CLI for AI YouTube Studio")
    subparsers = parser.add_subparsers(dest="command")

    # stats
    p_stats = subparsers.add_parser("stats", help="Xem báo cáo thống kê toàn diện")
    p_stats.add_argument("--json", action="store_true", help="Xuất định dạng JSON")

    # jobs
    p_jobs = subparsers.add_parser("jobs", help="Liệt kê danh sách jobs")
    p_jobs.add_argument("--status", type=str, help="Lọc theo trạng thái (SCRIPT_READY, RENDERED,...)")
    p_jobs.add_argument("--channel", type=str, help="Lọc theo ID kênh")
    p_jobs.add_argument("--limit", type=int, default=15, help="Số lượng jobs hiển thị")
    p_jobs.add_argument("--json", action="store_true", help="Xuất định dạng JSON")

    # job
    p_job = subparsers.add_parser("job", help="Xem chi tiết 1 job")
    p_job.add_argument("job_id", type=int, help="ID của VideoJob")

    # create
    p_create = subparsers.add_parser("create", help="Tạo kịch bản mới từ Topic")
    p_create.add_argument("--topic", type=str, required=True, help="Chủ đề video")
    p_create.add_argument("--channel", type=str, help="ID kênh đích")
    p_create.add_argument("--lang", type=str, help="Ngôn ngữ ('en' hoặc 'vi')")
    p_create.add_argument("--auto-drive", action="store_true", help="Tự động đẩy vào Drive sau khi tạo")

    # push-drive
    p_push = subparsers.add_parser("push-drive", help="Đẩy kịch bản vào Drive pending")
    p_push.add_argument("job_id", type=int, help="ID của VideoJob")

    # check-drive
    p_chk = subparsers.add_parser("check-drive", help="Kiểm tra video hoàn thành trên Drive")
    p_chk.add_argument("job_id", type=int, help="ID của VideoJob")

    # scan-drive
    subparsers.add_parser("scan-drive", help="Quét và nhập tất cả video đã xong từ Drive")

    # publish
    p_pub = subparsers.add_parser("publish", help="Đăng video lên YouTube Shorts")
    p_pub.add_argument("job_id", type=int, help="ID của VideoJob")

    # retry
    p_retry = subparsers.add_parser("retry", help="Đặt lại trạng thái job về SCRIPT_READY")
    p_retry.add_argument("job_id", type=int, help="ID của VideoJob")

    # cancel
    p_cancel = subparsers.add_parser("cancel", help="Hủy bỏ 1 job")
    p_cancel.add_argument("job_id", type=int, help="ID của VideoJob")

    # channels
    subparsers.add_parser("channels", help="Danh sách kênh YouTube")

    # logs
    p_logs = subparsers.add_parser("logs", help="Xem nhật ký hành động")
    p_logs.add_argument("--limit", type=int, default=15, help="Số lượng dòng nhật ký")

    # reset-stuck
    p_rst = subparsers.add_parser("reset-stuck", help="Khôi phục jobs bị treo")
    p_rst.add_argument("--minutes", type=int, default=30, help="Số phút ngưỡng để coi là treo")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "stats": cmd_stats,
        "jobs": cmd_jobs,
        "job": cmd_job,
        "create": cmd_create,
        "push-drive": cmd_push_drive,
        "check-drive": cmd_check_drive,
        "scan-drive": cmd_scan_drive,
        "publish": cmd_publish,
        "retry": cmd_retry,
        "cancel": cmd_cancel,
        "channels": cmd_channels,
        "logs": cmd_logs,
        "reset-stuck": cmd_reset_stuck
    }

    fn = cmds.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
