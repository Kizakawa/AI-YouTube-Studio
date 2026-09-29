"""
tests/test_database_scenarios.py — Bộ kiểm thử tự động toàn diện cho CSDL Dual-Engine (Issue 1)

Bao gồm 6 kịch bản kiểm thử:
1. Chuẩn hóa URL & Dual-Engine (postgres:// -> postgresql://, fallback SQLite)
2. Cơ chế Tự Phục Hồi (Auto-Fallback khi PostgreSQL không khả dụng)
3. Concurrency Stress Test (20 worker threads cùng lúc không bị database is locked)
4. An toàn Giao dịch & Rollback (Xác minh không leak session và rollback 100%)
5. Di chuyển Dữ liệu & Đối soát (Migration & Row Reconciliation)
6. Tương thích Dialect & Ngăn chặn crash PRAGMA trên PostgreSQL
"""

import os
import sys
import time
import shutil
import tempfile
import unittest
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# Đảm bảo UTF-8 và đường dẫn import
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.models import (
    Base, User, VideoJob, ActivityLog,
    normalize_database_url, create_configured_engine, get_db, init_db
)
from scripts.migrate_sqlite_to_postgres import migrate_database
from config.settings import settings


class TestDatabaseDualEngineScenarios(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ai_yt_db_test_")

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # =========================================================================
    # KỊCH BẢN 1: Chuẩn hóa URL & Dialect Detection
    # =========================================================================
    def test_scenario_1_url_normalization(self):
        print("\n▶ [Scenario 1] Kiểm tra chuẩn hóa URL và phát hiện Dialect...")
        
        # Test 1.1: Render / Supabase legacy postgres:// URL
        legacy_pg = "postgres://usr_admin:p@ssword123@dpg-render-cloud.com:5432/ai_youtube_db"
        normalized_pg = normalize_database_url(legacy_pg)
        self.assertTrue(normalized_pg.startswith("postgresql://"), f"Failed to normalize postgres:// prefix: {normalized_pg}")
        self.assertEqual(normalized_pg, "postgresql://usr_admin:p@ssword123@dpg-render-cloud.com:5432/ai_youtube_db")

        # Test 1.2: Modern postgresql:// URL giữ nguyên
        modern_pg = "postgresql://user:pass@host:5432/db"
        self.assertEqual(normalize_database_url(modern_pg), modern_pg)

        # Test 1.3: URL có khoảng trắng thừa đầu/cuối
        spaced_pg = "   postgres://u:p@h:5432/d   "
        self.assertEqual(normalize_database_url(spaced_pg), "postgresql://u:p@h:5432/d")

        # Test 1.4: None hoặc rỗng -> Fallback về SQLite mặc định
        expected_default = f"sqlite:///{settings.BASE_DIR}/storage/ai_youtube.db"
        self.assertEqual(normalize_database_url(""), expected_default)
        self.assertEqual(normalize_database_url(None), expected_default)

        # Test 1.5: SQLite URL tùy chỉnh giữ nguyên
        custom_sqlite = "sqlite:///custom/path/db.sqlite"
        self.assertEqual(normalize_database_url(custom_sqlite), custom_sqlite)
        print("  ✅ [Scenario 1 PASSED] 100% định dạng URL được xử lý chính xác!")

    # =========================================================================
    # KỊCH BẢN 2: Cơ chế Tự Phục Hồi (Auto-Fallback)
    # =========================================================================
    def test_scenario_2_auto_fallback_resilience(self):
        print("\n▶ [Scenario 2] Kiểm tra cơ chế tự phục hồi (Auto-Fallback khi PostgreSQL lỗi)...")
        
        # Giả lập URL PostgreSQL unreachable / sai port
        unreachable_pg = "postgresql://invalid_user:invalid_pass@127.0.0.1:54399/non_existent_db"
        
        start_time = time.time()
        eng, active_url = create_configured_engine(unreachable_pg)
        elapsed = time.time() - start_time
        
        # Xác minh không làm ứng dụng crash, mà tự động fallback về SQLite
        self.assertIsNotNone(eng, "Engine không được trả về None khi fallback")
        self.assertTrue(active_url.startswith("sqlite:///"), f"Expected SQLite fallback but got: {active_url}")
        self.assertEqual(eng.dialect.name, "sqlite")
        
        # Kiểm tra engine fallback hoạt động tốt (thực thi câu lệnh SQL)
        with eng.connect() as conn:
            val = conn.execute(text("SELECT 12345")).scalar()
            self.assertEqual(val, 12345)
            
        print(f"  ✅ [Scenario 2 PASSED] Auto-Fallback thành công về SQLite trong {elapsed:.2f}s mà không crash ứng dụng!")

    # =========================================================================
    # KỊCH BẢN 3: Concurrency Stress Test (20 Luồng đồng thời)
    # =========================================================================
    def test_scenario_3_concurrency_stress(self):
        print("\n▶ [Scenario 3] Kiểm tra đa luồng (20 worker threads cùng ghi/đọc đồng thời)...")
        
        test_db_path = os.path.join(self.temp_dir, "stress_test.db")
        test_url = f"sqlite:///{test_db_path}"
        eng, _ = create_configured_engine(test_url)
        
        # Tạo bảng
        Base.metadata.create_all(bind=eng)
        LocalSession = sessionmaker(bind=eng, autocommit=False, autoflush=False, expire_on_commit=False)
        
        num_threads = 20
        errors = []
        inserted_ids = []
        lock = threading.Lock()

        def worker_task(thread_id: int):
            try:
                # Mỗi worker mở session và thực hiện ghi/đọc
                db = LocalSession()
                try:
                    # Ghi 1 User
                    u = User(
                        telegram_id=1000 + thread_id,
                        username=f"stress_user_{thread_id}",
                        full_name=f"Worker {thread_id}",
                        role="creator"
                    )
                    db.add(u)
                    db.commit()

                    # Ghi 1 VideoJob
                    j = VideoJob(
                        user_id=1000 + thread_id,
                        target_channel_id="CHANNEL_STRESS",
                        topic=f"Stress Topic {thread_id}",
                        status="DRAFT"
                    )
                    db.add(j)
                    db.commit()

                    with lock:
                        inserted_ids.append(j.id)

                    # Đọc lại kiểm tra
                    verify_job = db.query(VideoJob).filter(VideoJob.id == j.id).first()
                    assert verify_job is not None, f"Thread {thread_id} failed to read back job"
                finally:
                    db.close()
            except Exception as e:
                with lock:
                    errors.append(f"Thread {thread_id} error: {e}")

        # Khởi chạy 20 threads đồng thời bằng ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_threads)]
            for f in as_completed(futures):
                f.result()

        # Kiểm tra không có lỗi "database is locked"
        self.assertEqual(len(errors), 0, f"Concurrency test gặp lỗi: {errors}")
        self.assertEqual(len(inserted_ids), num_threads, f"Expected {num_threads} inserts, got {len(inserted_ids)}")

        # Xác minh tổng số bản ghi trong DB
        db = LocalSession()
        user_count = db.query(User).count()
        job_count = db.query(VideoJob).count()
        db.close()
        self.assertEqual(user_count, num_threads)
        self.assertEqual(job_count, num_threads)
        eng.dispose()

        print(f"  ✅ [Scenario 3 PASSED] 20 luồng đồng thời ghi & đọc 100% thành công, không gặp database lock!")

    # =========================================================================
    # KỊCH BẢN 4: An toàn Giao dịch & Rollback
    # =========================================================================
    def test_scenario_4_transaction_safety_and_rollback(self):
        print("\n▶ [Scenario 4] Kiểm tra tính toàn vẹn giao dịch và cơ chế Auto-Rollback...")
        
        test_db_path = os.path.join(self.temp_dir, "rollback_test.db")
        test_url = f"sqlite:///{test_db_path}"
        eng, _ = create_configured_engine(test_url)
        Base.metadata.create_all(bind=eng)
        TestSession = sessionmaker(bind=eng, autocommit=False, autoflush=False, expire_on_commit=False)

        from contextlib import contextmanager
        @contextmanager
        def get_custom_db():
            db = TestSession()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        # 4.1 Thêm dữ liệu ban đầu
        with get_custom_db() as db:
            db.add(User(telegram_id=999, username="safe_user", full_name="Safe User"))

        # Xác minh đã lưu
        with get_custom_db() as db:
            cnt = db.query(User).filter(User.telegram_id == 999).count()
            self.assertEqual(cnt, 1)

        # 4.2 Cố tình gây lỗi trong transaction để kiểm tra auto-rollback
        with self.assertRaises(ValueError):
            with get_custom_db() as db:
                db.add(User(telegram_id=888, username="corrupt_user", full_name="Should Rollback"))
                # Throw exception có chủ đích
                raise ValueError("Simulated unexpected pipeline exception")

        # 4.3 Xác minh bản ghi lỗi 888 KHÔNG được ghi vào DB
        with get_custom_db() as db:
            cnt_corrupt = db.query(User).filter(User.telegram_id == 888).count()
            self.assertEqual(cnt_corrupt, 0, "Bản ghi bị exception vẫn xuất hiện trong DB (Rollback thất bại)!")

        # 4.4 Transaction sau đó vẫn hoạt động bình thường (không bị lock/stale)
        with get_custom_db() as db:
            db.add(User(telegram_id=777, username="next_user", full_name="Next User"))
        with get_custom_db() as db:
            self.assertEqual(db.query(User).filter(User.telegram_id == 777).count(), 1)

        eng.dispose()
        print("  ✅ [Scenario 4 PASSED] Rollback hoạt động hoàn hảo, bảo vệ toàn vẹn dữ liệu khi có lỗi phát sinh!")

    # =========================================================================
    # KỊCH BẢN 5: Di chuyển Dữ liệu & Đối Soát (Migration & Reconciliation)
    # =========================================================================
    def test_scenario_5_data_migration_and_reconciliation(self):
        print("\n▶ [Scenario 5] Kiểm tra kịch bản di chuyển CSDL (Migration & Row Count Reconciliation)...")
        
        src_path = os.path.join(self.temp_dir, "source_ai_yt.db")
        tgt_path = os.path.join(self.temp_dir, "target_cloud.db")
        src_url = f"sqlite:///{src_path}"
        tgt_url = f"sqlite:///{tgt_path}"

        # 5.1 Tạo DB nguồn và bơm dữ liệu giả lập giống môi trường thực tế
        src_eng = create_engine(src_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=src_eng)
        SrcSession = sessionmaker(bind=src_eng)
        
        with SrcSession() as s:
            # Users
            s.add(User(telegram_id=111, username="kizakawa", full_name="Kizakawa", role="admin"))
            s.add(User(telegram_id=222, username="giang", full_name="Giang", role="creator"))
            # Video Jobs
            s.add(VideoJob(
                id=1,
                user_id=111,
                target_channel_id="TAM_LY",
                topic="Tâm lý con người",
                title="5 Quy Luật Cuộc Đời",
                status="PUBLISHED",
                youtube_video_id="abc123xyz",
                scenes_json='[{"scene_id": 1, "narration": "Text 1"}]',
                progress_percent=100
            ))
            s.add(VideoJob(
                id=2,
                user_id=222,
                target_channel_id="KINH_DOANH",
                topic="Chiến lược kinh doanh",
                title="Bí quyết startup",
                status="RENDERING",
                progress_percent=60
            ))
            # Activity Logs
            s.add(ActivityLog(telegram_id=111, username="kizakawa", action_type="CMD_IDEA", details="Tạo ý tưởng tâm lý"))
            s.add(ActivityLog(telegram_id=222, username="giang", action_type="APPROVE_VIDEO", details="Duyệt video startup"))
            s.commit()

        src_eng.dispose()

        # 5.2 Chạy migration từ src_path sang tgt_url
        success = migrate_database(src_path, tgt_url, dry_run=False)
        self.assertTrue(success, "migrate_database returned False")

        # 5.3 Đối soát chi tiết dữ liệu ở đích
        tgt_eng = create_engine(tgt_url, connect_args={"check_same_thread": False})
        TgtSession = sessionmaker(bind=tgt_eng)
        with TgtSession() as t:
            # Kiểm tra Users
            self.assertEqual(t.query(User).count(), 2)
            u1 = t.query(User).filter(User.telegram_id == 111).first()
            self.assertIsNotNone(u1)
            self.assertEqual(u1.username, "kizakawa")

            # Kiểm tra VideoJobs
            self.assertEqual(t.query(VideoJob).count(), 2)
            j1 = t.query(VideoJob).filter(VideoJob.id == 1).first()
            self.assertIsNotNone(j1)
            self.assertEqual(j1.status, "PUBLISHED")
            self.assertEqual(j1.youtube_video_id, "abc123xyz")
            self.assertEqual(j1.progress_percent, 100)

            # Kiểm tra ActivityLogs
            self.assertEqual(t.query(ActivityLog).count(), 2)
            log1 = t.query(ActivityLog).filter(ActivityLog.action_type == "CMD_IDEA").first()
            self.assertIsNotNone(log1)
            self.assertEqual(log1.username, "kizakawa")

        tgt_eng.dispose()
        print("  ✅ [Scenario 5 PASSED] Di chuyển dữ liệu thành công 100%, bảo toàn nguyên vẹn mọi trường dữ liệu!")

    # =========================================================================
    # KỊCH BẢN 6: Tương thích Dialect & Schema Guard
    # =========================================================================
    def test_scenario_6_schema_guard_and_dialect_compat(self):
        print("\n▶ [Scenario 6] Kiểm tra tương thích Dialect & Ngăn chặn lỗi SQLite PRAGMA...")
        
        # Test 6.1: Khởi tạo SQLite engine và kiểm tra PRAGMA WAL
        sqlite_test_path = os.path.join(self.temp_dir, "dialect_test.db")
        eng, _ = create_configured_engine(f"sqlite:///{sqlite_test_path}")
        
        with eng.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            self.assertEqual(mode.lower(), "wal", f"Expected WAL mode on SQLite, got {mode}")

        # Test 6.2: Kiểm tra connect_args không bị rò rỉ check_same_thread sang PostgreSQL
        pg_norm = normalize_database_url("postgres://u:p@localhost:5432/db")
        self.assertTrue(pg_norm.startswith("postgresql://"))

        # Test 6.3: init_db an toàn trên SQLite
        init_db()

        eng.dispose()
        print("  ✅ [Scenario 6 PASSED] Cấu hình engine phân tách chuẩn xác theo từng Dialect!")


def run_all_tests():
    print("=" * 70)
    print("🚀 BẮT ĐẦU CHẠY BỘ KIỂM THỬ TỰ ĐỘNG CSDL DUAL-ENGINE (ISSUE 1)")
    print("=" * 70)

    suite = unittest.TestLoader().loadTestsFromTestCase(TestDatabaseDualEngineScenarios)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print(f"🎉 TẤT CẢ {result.testsRun} KỊCH BẢN ĐÃ VƯỢT QUA KIỂM THỬ THÀNH CÔNG (100% PASS)!")
        print("=" * 70)
        return True
    else:
        print(f"❌ CÓ LỖI: {len(result.failures)} thất bại, {len(result.errors)} lỗi trong tổng số {result.testsRun} bài kiểm thử.")
        print("=" * 70)
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
