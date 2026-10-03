"""
tests/test_quota_scenarios.py — Bộ kiểm thử tự động toàn diện cho Quản lý Quota YouTube API v3 (Issue 3)

Bao gồm 5 kịch bản kiểm thử:
1. Tính toán Chu kỳ Quota Google (Reset lúc 14:00 VN / 00:00 PT).
2. Quota Guard (Pre-flight check ngăn chặn gọi API khi còn dưới 1.650 units).
3. Bắt lỗi 403 QuotaExceeded và tự động chuyển sang WAITING_QUOTA_RESET.
4. Tách biệt Quota giữa các Google Cloud Projects (Multi-Project GCP Pool).
5. Tự động xả hàng đợi và đăng video bù khi có Quota mới (Queue Auto-Flush).
"""

import sys
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.models import get_db, QuotaTracker, VideoJob, Base, engine
from app.services.quota_manager import (
    QuotaManager, get_current_cycle_id, get_next_reset_time,
    COST_FULL_SHORTS, DEFAULT_DAILY_LIMIT, VN_TZ
)
from app.agents.publisher_agent import PublisherAgent
from app.services.smart_scheduler import process_waiting_quota_jobs


class TestYouTubeQuotaScenarios(unittest.TestCase):
    def setUp(self):
        # Đảm bảo bảng quota_trackers tồn tại
        Base.metadata.create_all(bind=engine)
        self.test_project = f"test_proj_{int(datetime.now(timezone.utc).timestamp())}"
        self.test_cycle = get_current_cycle_id()

    def tearDown(self):
        # Dọn dẹp dữ liệu test
        with get_db() as db:
            db.query(QuotaTracker).filter(QuotaTracker.project_id == self.test_project).delete()
            db.query(VideoJob).filter(VideoJob.topic.like("QUOTA_TEST_%")).delete()

    # =========================================================================
    # KỊCH BẢN 1: Chu kỳ Quota Google (Reset lúc 14:00 VN)
    # =========================================================================
    def test_scenario_1_quota_cycle_calculation(self):
        print("\n▶ [Scenario 1] Kiểm tra tính toán chu kỳ Reset Quota của Google (14:00 VN)...")
        
        # 1.1 Kiểm tra mốc 10:00 sáng VN ngày 2026-09-30 (trước 14:00 -> chu kỳ hôm qua 2026-09-29)
        dt_morning = datetime(2026, 9, 30, 10, 0, 0, tzinfo=VN_TZ)
        cycle_morning = get_current_cycle_id(dt_morning)
        self.assertEqual(cycle_morning, "2026-09-29")

        # 1.2 Kiểm tra mốc 14:05 chiều VN ngày 2026-09-30 (sau 14:00 -> chu kỳ hôm nay 2026-09-30)
        dt_afternoon = datetime(2026, 9, 30, 14, 5, 0, tzinfo=VN_TZ)
        cycle_afternoon = get_current_cycle_id(dt_afternoon)
        self.assertEqual(cycle_afternoon, "2026-09-30")

        # 1.3 Kiểm tra mốc reset tiếp theo
        reset_time_morning = get_next_reset_time(dt_morning)
        self.assertEqual(reset_time_morning.hour, 14)
        self.assertEqual(reset_time_morning.day, 30)

        reset_time_afternoon = get_next_reset_time(dt_afternoon)
        self.assertEqual(reset_time_afternoon.hour, 14)
        self.assertEqual(reset_time_afternoon.day, 1) # Ngày mai

        print("  ✅ [Scenario 1 PASSED] Thuật toán đồng bộ chu kỳ 14:00 VN chuẩn xác 100%!")

    # =========================================================================
    # KỊCH BẢN 2: Quota Guard & Giới hạn Ngưỡng An toàn
    # =========================================================================
    def test_scenario_2_quota_guard_preflight(self):
        print("\n▶ [Scenario 2] Kiểm tra cơ chế Quota Guard (Pre-flight check chặn chạm trần)...")

        # Khởi đầu dự án mới
        self.assertTrue(QuotaManager.can_upload(self.test_project))

        # Tiêu hao 5 video: 5 * 1650 = 8250 units
        for i in range(5):
            QuotaManager.record_consumption(self.test_project, units=COST_FULL_SHORTS, is_upload=True)

        info = QuotaManager.get_tracker(self.test_project)
        self.assertEqual(info["used_units"], 8250)
        self.assertEqual(info["upload_count"], 5)
        self.assertEqual(info["remaining_units"], 1750)
        self.assertTrue(info["can_upload"], "Còn 1750 units phải cho upload video thứ 6")

        # Tiêu hao tiếp video thứ 6: 8250 + 1650 = 9900 units
        QuotaManager.record_consumption(self.test_project, units=COST_FULL_SHORTS, is_upload=True)
        info_full = QuotaManager.get_tracker(self.test_project)
        self.assertEqual(info_full["used_units"], 9900)
        self.assertEqual(info_full["upload_count"], 6)
        self.assertEqual(info_full["remaining_units"], 100)

        # Quota Guard: Kiểm tra video thứ 7 -> PHẢI CHẶN LẠI ĐỂ KHÔNG BỊ LỖI 403
        self.assertFalse(
            QuotaManager.can_upload(self.test_project, required_units=COST_FULL_SHORTS),
            "Quota Guard phải từ chối khi chỉ còn 100 units!"
        )
        print("  ✅ [Scenario 2 PASSED] Quota Guard chặn chính xác sau 6 video (9.900 units), bảo vệ an toàn!")

    # =========================================================================
    # KỊCH BẢN 3: Bắt lỗi 403 QuotaExceeded & Chuyển sang WAITING_QUOTA_RESET
    # =========================================================================
    def test_scenario_3_quota_exceeded_interception(self):
        print("\n▶ [Scenario 3] Kiểm tra bắt lỗi 403 QuotaExceeded và tự chuyển trạng thái an toàn...")

        agent = PublisherAgent(channel_config={"id": "test_ch", "name": "Test Channel"})
        agent.project_id = self.test_project
        agent.service = MagicMock()

        # Tạo file video giả lập
        dummy_video = Path("storage/test_dummy_quota.mp4")
        dummy_video.parent.mkdir(parents=True, exist_ok=True)
        dummy_video.write_bytes(b"dummy_video_bytes")

        try:
            # Giả lập Google ném lỗi 403 QuotaExceeded
            agent.service.videos().insert.side_effect = Exception(
                "<HttpError 403 when requesting https://youtube.googleapis.com/... returned 'The request cannot be completed because you have exceeded your quota.'>"
            )

            res = agent.upload_video(
                video_path=str(dummy_video),
                title="Test Video Quota",
                description="Test Description"
            )

            # Xác minh phản hồi không làm sập bot mà chuyển sang waiting_quota_reset
            self.assertEqual(res["status"], "waiting_quota_reset")
            self.assertIn("chạm trần", res["error"])

            # Xác minh QuotaManager đã đánh dấu dự án cạn kiệt
            info = QuotaManager.get_tracker(self.test_project)
            self.assertEqual(info["used_units"], DEFAULT_DAILY_LIMIT)
            self.assertFalse(info["can_upload"])

            print("  ✅ [Scenario 3 PASSED] Bắt lỗi 403 mượt mà, chuyển trạng thái waiting_quota_reset thành công!")
        finally:
            import gc
            gc.collect()
            if dummy_video.exists():
                try:
                    dummy_video.unlink()
                except Exception:
                    pass

    # =========================================================================
    # KỊCH BẢN 4: Tách biệt Quota giữa các Google Cloud Projects
    # =========================================================================
    def test_scenario_4_multi_project_isolation(self):
        print("\n▶ [Scenario 4] Kiểm tra tính độc lập Quota giữa các Dự Án (Multi-Project Isolation)...")

        proj_a = f"{self.test_project}_A"
        proj_b = f"{self.test_project}_B"

        try:
            # Dự án A dùng hết 6 video (9900 units)
            QuotaManager.record_consumption(proj_a, units=9900, is_upload=True)
            self.assertFalse(QuotaManager.can_upload(proj_a))

            # Dự án B mới tinh (0 units)
            self.assertTrue(QuotaManager.can_upload(proj_b))
            self.assertEqual(QuotaManager.get_tracker(proj_b)["used_units"], 0)

            # Dự án B vẫn upload bình thường mà không bị ảnh hưởng bởi Dự án A
            QuotaManager.record_consumption(proj_b, units=COST_FULL_SHORTS, is_upload=True)
            self.assertEqual(QuotaManager.get_tracker(proj_b)["used_units"], 1650)
            self.assertTrue(QuotaManager.can_upload(proj_b))

            print("  ✅ [Scenario 4 PASSED] Quota 2 dự án hoàn toàn độc lập, nâng gấp đôi năng lực upload!")
        finally:
            with get_db() as db:
                db.query(QuotaTracker).filter(QuotaTracker.project_id.in_([proj_a, proj_b])).delete()

    # =========================================================================
    # KỊCH BẢN 5: Tự động Xả Hàng Đợi (Queue Auto-Flush) khi có Quota
    # =========================================================================
    def test_scenario_5_queue_auto_flush(self):
        print("\n▶ [Scenario 5] Kiểm tra tự động xả hàng đợi khi có Quota (Auto-Flush Queue)...")

        # Tạo file video giả lập
        dummy_video = Path("storage/test_queue_flush.mp4")
        dummy_video.parent.mkdir(parents=True, exist_ok=True)
        dummy_video.write_bytes(b"dummy_flush_content")

        test_job_id = None
        try:
            # Tạo 1 Job ở trạng thái WAITING_QUOTA_RESET trong CSDL
            with get_db() as db:
                job = VideoJob(
                    target_channel_id="channel_maya",
                    topic="QUOTA_TEST_TOPIC",
                    title="Video Cho Nap Quota",
                    status="WAITING_QUOTA_RESET",
                    video_path=str(dummy_video)
                )
                db.add(job)
                db.commit()
                test_job_id = job.id

            # Mock PublisherAgent.upload_video và QuotaManager.can_upload thành công khi Quota được phục hồi
            with patch("app.agents.publisher_agent.PublisherAgent.upload_video") as mock_upload, \
                 patch("app.services.quota_manager.QuotaManager.can_upload", return_value=True):
                mock_upload.return_value = {
                    "status": "success",
                    "video_id": "yt_flush_123",
                    "youtube_url": "https://youtu.be/yt_flush_123",
                    "shorts_url": "https://www.youtube.com/shorts/yt_flush_123"
                }

                # Chạy hàm quét tự động
                flushed = process_waiting_quota_jobs()
                self.assertGreaterEqual(flushed, 1, "Phải xử lý ít nhất 1 video trong hàng đợi")

                # Kiểm tra trạng thái Job trong DB đã đổi thành PUBLISHED
                with get_db() as db:
                    check_job = db.query(VideoJob).filter(VideoJob.id == test_job_id).first()
                    self.assertIsNotNone(check_job)
                    self.assertEqual(check_job.status, "PUBLISHED")
                    self.assertEqual(check_job.youtube_video_id, "yt_flush_123")

            print("  ✅ [Scenario 5 PASSED] Hàng đợi tự động kích hoạt và đăng bù video thành công 100%!")
        finally:
            import gc
            gc.collect()
            if dummy_video.exists():
                try:
                    dummy_video.unlink()
                except Exception:
                    pass
            if test_job_id:
                with get_db() as db:
                    db.query(VideoJob).filter(VideoJob.id == test_job_id).delete()


def run_all_tests():
    print("=" * 70)
    print("🚀 BẮT ĐẦU CHẠY BỘ KIỂM THỬ QUẢN LÝ QUOTA YOUTUBE API (ISSUE 3)")
    print("=" * 70)

    suite = unittest.TestLoader().loadTestsFromTestCase(TestYouTubeQuotaScenarios)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print(f"🎉 TẤT CẢ {result.testsRun} KỊCH BẢN QUOTA ĐÃ VƯỢT QUA KIỂM THỬ THÀNH CÔNG (100% PASS)!")
        print("=" * 70)
        return True
    else:
        print(f"❌ CÓ LỖI: {len(result.failures)} thất bại, {len(result.errors)} lỗi trong tổng số {result.testsRun} bài kiểm thử.")
        print("=" * 70)
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
