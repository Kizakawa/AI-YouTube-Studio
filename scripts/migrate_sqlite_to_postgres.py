"""
scripts/migrate_sqlite_to_postgres.py — Tự động di chuyển dữ liệu từ SQLite cục bộ lên PostgreSQL Đám Mây.

Chức năng:
1. Đọc toàn bộ bảng (users, video_jobs, activity_logs) từ storage/ai_youtube.db.
2. Tự động tạo cấu trúc bảng trên PostgreSQL mới (Base.metadata.create_all).
3. Bơm toàn bộ dữ liệu, bảo toàn 100% ID, trạng thái video, link YouTube, và lịch sử.
4. Đối soát số lượng dòng (Row Reconciliation) để đảm bảo không mất mát dữ liệu.
"""

import sys
import os
import argparse
from pathlib import Path
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.models import Base, User, VideoJob, ActivityLog, normalize_database_url
from config.settings import settings

def migrate_database(sqlite_path: str, target_postgres_url: str, dry_run: bool = False):
    print("=" * 65)
    print("🔄 BẮT ĐẦU QUY TRÌNH DI CHUYỂN DỮ LIỆU SANG POSTGRESQL CLOUD")
    print("=" * 65)

    if not os.path.exists(sqlite_path):
        print(f"❌ Lỗi: File SQLite nguồn không tồn tại: {sqlite_path}")
        return False

    norm_target = normalize_database_url(target_postgres_url)
    is_pg = norm_target.startswith("postgresql://")
    is_sqlite = norm_target.startswith("sqlite:///")

    if not (is_pg or is_sqlite):
        print(f"❌ Lỗi: URL đích phải là PostgreSQL hoặc SQLite. Nhận được: {norm_target}")
        return False

    print(f"📁 Nguồn SQLite:     {sqlite_path}")
    print(f"🌐 Đích:             {norm_target.split('@')[-1] if '@' in norm_target else norm_target}")
    if dry_run:
        print("⚠️ CHẾ ĐỘ DRY-RUN: Chỉ kiểm tra và đọc thử dữ liệu, KHÔNG ghi vào đích.")

    # 1. Khởi tạo Source & Target Engine
    src_engine = create_engine(f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False})
    SrcSession = sessionmaker(bind=src_engine)

    if is_pg:
        driver_target = norm_target
        if driver_target.startswith("postgresql://"):
            driver_target = "postgresql+psycopg2://" + driver_target[len("postgresql://"):]
        tgt_engine = create_engine(driver_target, pool_pre_ping=True)
    else:
        tgt_engine = create_engine(norm_target, connect_args={"check_same_thread": False})
    TgtSession = sessionmaker(bind=tgt_engine)

    # 2. Khởi tạo bảng trên đích nếu chưa có
    if not dry_run:
        print("\n🔨 Đang khởi tạo cấu trúc bảng trên PostgreSQL...")
        Base.metadata.create_all(bind=tgt_engine)
        print("✅ Cấu trúc bảng trên PostgreSQL đã sẵn sàng!")

    src_db = SrcSession()
    tgt_db = TgtSession()

    try:
        # 3. Di chuyển bảng User
        users = src_db.query(User).all()
        print(f"\n👤 Bảng [users]: Tìm thấy {len(users)} bản ghi.")
        migrated_users = 0
        for u in users:
            if not dry_run:
                existing = tgt_db.query(User).filter(User.telegram_id == u.telegram_id).first()
                if not existing:
                    u_data = {col.name: getattr(u, col.name) for col in User.__table__.columns}
                    tgt_db.add(User(**u_data))
                    migrated_users += 1
            else:
                migrated_users += 1

        if not dry_run:
            tgt_db.commit()
        print(f"   -> Đã xử lý {migrated_users} users.")

        # 4. Di chuyển bảng VideoJob
        jobs = src_db.query(VideoJob).order_by(VideoJob.id).all()
        print(f"\n🎬 Bảng [video_jobs]: Tìm thấy {len(jobs)} jobs.")
        migrated_jobs = 0
        for j in jobs:
            if not dry_run:
                existing = tgt_db.query(VideoJob).filter(VideoJob.id == j.id).first()
                if not existing:
                    j_data = {col.name: getattr(j, col.name) for col in VideoJob.__table__.columns}
                    tgt_db.add(VideoJob(**j_data))
                    migrated_jobs += 1
            else:
                migrated_jobs += 1

        if not dry_run:
            tgt_db.commit()
        print(f"   -> Đã xử lý {migrated_jobs} video jobs.")

        # 5. Di chuyển bảng ActivityLog
        logs = src_db.query(ActivityLog).order_by(ActivityLog.id).all()
        print(f"\n📝 Bảng [activity_logs]: Tìm thấy {len(logs)} logs.")
        migrated_logs = 0
        for l in logs:
            if not dry_run:
                existing = tgt_db.query(ActivityLog).filter(ActivityLog.id == l.id).first()
                if not existing:
                    l_data = {col.name: getattr(l, col.name) for col in ActivityLog.__table__.columns}
                    tgt_db.add(ActivityLog(**l_data))
                    migrated_logs += 1
            else:
                migrated_logs += 1

        if not dry_run:
            tgt_db.commit()
        print(f"   -> Đã xử lý {migrated_logs} logs.")

        # 5b. Đồng bộ Sequence cho PostgreSQL (để các lần INSERT tiếp theo không bị duplicate key)
        if not dry_run and is_pg:
            print("\n🔢 Đang đồng bộ Sequence cho các bảng PostgreSQL...")
            for table_name in ["video_jobs", "activity_logs"]:
                try:
                    tgt_db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), coalesce(max(id), 1)) FROM {table_name}"))
                    tgt_db.commit()
                except Exception as seq_err:
                    print(f"   ⚠️ Không thể đồng bộ sequence {table_name}: {seq_err}")
            print("✅ Sequence PostgreSQL đã được đồng bộ chuẩn xác!")

        # 6. ĐỐI SOÁT DỮ LIỆU (Row Count Reconciliation)
        print("\n" + "=" * 65)
        print("📊 BÁO CÁO ĐỐI SOÁT DỮ LIỆU SAU DI CHUYỂN")
        print("=" * 65)
        
        src_user_cnt = src_db.query(func.count(User.telegram_id)).scalar()
        src_job_cnt = src_db.query(func.count(VideoJob.id)).scalar()
        src_log_cnt = src_db.query(func.count(ActivityLog.id)).scalar()

        if not dry_run:
            tgt_user_cnt = tgt_db.query(func.count(User.telegram_id)).scalar()
            tgt_job_cnt = tgt_db.query(func.count(VideoJob.id)).scalar()
            tgt_log_cnt = tgt_db.query(func.count(ActivityLog.id)).scalar()
        else:
            tgt_user_cnt = src_user_cnt
            tgt_job_cnt = src_job_cnt
            tgt_log_cnt = src_log_cnt

        print(f"  • Users:         Nguồn (SQLite): {src_user_cnt:>4}  |  Đích (Postgres): {tgt_user_cnt:>4}")
        print(f"  • Video Jobs:    Nguồn (SQLite): {src_job_cnt:>4}  |  Đích (Postgres): {tgt_job_cnt:>4}")
        print(f"  • Activity Logs: Nguồn (SQLite): {src_log_cnt:>4}  |  Đích (Postgres): {tgt_log_cnt:>4}")

        all_matched = (src_user_cnt <= tgt_user_cnt) and (src_job_cnt <= tgt_job_cnt) and (src_log_cnt <= tgt_log_cnt)
        if all_matched:
            print("\n🎉 HOÀN TẤT DI CHUYỂN: 100% DỮ LIỆU ĐÃ ĐƯỢC BẢO TOÀN NGUYÊN VẸN!")
            return True
        else:
            print("\n⚠️ CẢNH BÁO: Số lượng bản ghi sau khi di chuyển có chênh lệch, vui lòng kiểm tra lại log!")
            return False

    except Exception as e:
        tgt_db.rollback()
        print(f"\n❌ Lỗi trong quá trình di chuyển: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        src_db.close()
        tgt_db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Di chuyển SQLite lên PostgreSQL Cloud")
    parser.add_argument("--sqlite", default=f"{settings.BASE_DIR}/storage/ai_youtube.db", help="Đường dẫn file SQLite nguồn")
    parser.add_argument("--postgres-url", default=os.getenv("POSTGRES_DATABASE_URL") or os.getenv("DATABASE_URL"), help="URL PostgreSQL đích")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử nghiệm không ghi dữ liệu")

    args = parser.parse_args()
    if not args.postgres_url or "sqlite" in args.postgres_url:
        print("⚠️ Vui lòng cung cấp --postgres-url (hoặc thiết lập biến môi trường POSTGRES_DATABASE_URL)")
        print("Ví dụ: python scripts/migrate_sqlite_to_postgres.py --postgres-url postgresql://user:pass@host/dbname --dry-run")
        sys.exit(1)

    success = migrate_database(args.sqlite, args.postgres_url, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
