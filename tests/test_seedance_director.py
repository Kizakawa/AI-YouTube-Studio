"""
Test Suite for SeedanceDirector (Seedance 2.0 Skill OS Integration).
Verifies:
  1. Physical Anchor Converter (Metaphor-to-Action translation in Vietnamese & English).
  2. Idle Business Injector (Anti-mannequin freeze ambient life).
  3. The Lock Line Rule (Continuity locking across cuts).
  4. Shot Table Builder (Floor Plan, Camera, Eyeline, Action, Light, Last Frame).
  5. StoryboardManager integration (get_shot_table).
  6. FastAPI endpoint (GET /api/videos/{id}/shot-table).
  7. Fail-safe edge cases & fallback stability.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.database.models import init_db, get_db, VideoJob, User
from app.services.media.seedance_director import SeedanceDirector
from app.services.storyboard_manager import StoryboardManager
from fastapi.testclient import TestClient
from app.web.server import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    init_db()
    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 0).first()
        if not u:
            db.add(User(telegram_id=0, username="seedance_tester", role="admin"))
            db.commit()


def test_physical_anchor_enrichment_vietnamese():
    """Kiểm tra chuyển đổi các từ khóa cảm xúc/ẩn dụ tiếng Việt thành điểm neo thể chất."""
    director = SeedanceDirector()

    # 1. Ẩn dụ thất vọng / sa sầm
    p1 = director.enrich_with_physical_anchor("Cảnh nhân vật chính mặt sa sầm khi nghe tin", "anh ấy quá thất vọng")
    assert "eyes slowly casting downward" in p1 or "shoulders dropping" in p1

    # 2. Sốc / chết lặng
    p2 = director.enrich_with_physical_anchor("Cô ấy chết lặng đứng ở cửa", "quá sốc trước sự thật")
    assert "freezes completely" in p2 or "Adam's apple" in p2

    # 3. Tức giận / giận dữ
    p3 = director.enrich_with_physical_anchor("Người đàn ông tức giận phản bác", "cơn giận dữ bùng nổ")
    assert "jaw clenching hard" in p3 or "fists tightening" in p3


def test_physical_anchor_enrichment_english():
    """Kiểm tra chuyển đổi các ẩn dụ tiếng Anh thành điểm neo thể chất."""
    director = SeedanceDirector()

    # 1. "shocked and stunned"
    p1 = director.enrich_with_physical_anchor("Manager looks shocked at the customer", "he was stunned")
    assert "physical anchor:" in p1
    assert "freezes completely" in p1 or "eyes wide" in p1

    # 2. "heartbroken"
    p2 = director.enrich_with_physical_anchor("A heartbroken mother at the hospital", "weeping silently")
    assert "physical anchor:" in p2
    assert "tears" in p2 or "tremor" in p2


def test_idle_business_environmental_injection():
    """Kiểm tra cơ chế Anti-Mannequin Freeze bổ sung hành động nền & môi trường."""
    director = SeedanceDirector()

    # 1. Ngữ cảnh bão / mưa
    p_storm = director.inject_idle_business("A fisherman on the pier during a heavy storm with ocean waves")
    assert "ambient environment:" in p_storm
    assert "wind driving" in p_storm or "mist" in p_storm

    # 2. Ngữ cảnh phòng trong nhà
    p_indoor = director.inject_idle_business("Two businessmen sitting inside a meeting room drinking coffee")
    assert "ambient indoor life:" in p_indoor
    assert "breathing" in p_indoor or "dust" in p_indoor

    # 3. Ngữ cảnh viễn tưởng / không gian
    p_scifi = director.inject_idle_business("An astronaut exploring alien ruins in deep space")
    assert "ambient futuristic atmosphere:" in p_scifi
    assert "console" in p_scifi or "particles" in p_scifi


def test_lock_line_rule_generation_and_attachment():
    """Kiểm tra nguyên tắc The Lock Line Rule để giữ tính nhất quán 100% giữa các phân cảnh."""
    director = SeedanceDirector()

    lock_line = director.build_lock_line(
        entity_name="Dr. Maya Vance",
        visual_traits="thirties, dark hair in ponytail, white lab coat",
        lighting="directional cool blue fluorescent key light",
        camera_style="35mm cinematic vertical framing"
    )

    assert "Lock Line:" in lock_line
    assert "Dr. Maya Vance" in lock_line
    assert "white lab coat" in lock_line
    assert "cool blue fluorescent" in lock_line

    # Gắn vào prompt phân cảnh
    prompt = "Shot 1. Close-up of Maya examining the glowing flask"
    final_prompt = director.apply_lock_line_to_scene(prompt, lock_line)
    assert final_prompt.endswith(lock_line)
    assert "Shot 1. Close-up of Maya examining the glowing flask." in final_prompt

    # Không lặp lại nếu đã có
    repeat_prompt = director.apply_lock_line_to_scene(final_prompt, lock_line)
    assert repeat_prompt.count("Lock Line:") == 1


def test_create_shot_table_comprehensive():
    """Kiểm tra dựng Bảng Hình Học Phân Cảnh (Shot Table) đầy đủ các trường đạo diễn."""
    director = SeedanceDirector()

    scenes = [
        {
            "scene_id": 1,
            "narration": "Cô gái đẩy cánh cửa bước vào tiệm đồ hiệu sang trọng trong cơn mưa lớn.",
            "visual_keyword": "tiệm đồ hiệu xa hoa cô gái ướt sũng",
            "estimated_seconds": 3.5
        },
        {
            "scene_id": 2,
            "narration": "Người quản lý sững sờ khi nhận được cuộc gọi từ chủ tịch.",
            "visual_keyword": "quản lý chết lặng nghe điện thoại",
            "estimated_seconds": 4.0
        },
        {
            "scene_id": 3,
            "narration": "Dấu giày bùn in rõ trên tấm thảm trắng tinh, kết thúc câu chuyện.",
            "visual_keyword": "dấu giày bùn trên thảm trắng",
            "estimated_seconds": 3.0
        }
    ]

    table = director.create_shot_table(
        scenes=scenes,
        primary_entity="Cô gái trẻ",
        topic="Cú Lật Kèo Ở Tiệm Đồ Hiệu"
    )

    assert table["shot_count"] == 3
    assert "Floor Plan" in table["floor_plan"]
    assert table["primary_entity"] == "Cô gái trẻ"

    # Kiểm tra Shot 1 (Establishing Wide)
    s1 = table["shots"][0]
    assert s1["shot_id"] == 1
    assert "Wide establishing shot" in s1["camera"]
    assert "Eyeline" in s1["eyeline"]
    assert "others_idle_business" in s1
    assert "Lock Line:" in s1["lock_line"]

    # Kiểm tra Shot 2 (Profile / Medium with Physical Anchor for 'sững sờ/chết lặng')
    s2 = table["shots"][1]
    assert s2["shot_id"] == 2
    assert "freezes completely" in s2["action"] or "eyes wide" in s2["action"]

    # Kiểm tra Shot 3 (Concluded Insert)
    s3 = table["shots"][2]
    assert s3["shot_id"] == 3
    assert "Tight low-angle insert" in s3["camera"]


def test_storyboard_manager_get_shot_table_integration():
    """Kiểm tra tích hợp get_shot_table trong StoryboardManager."""
    mgr = StoryboardManager()

    test_scenes = [
        {"scene_id": 1, "narration": "Hook beat 1", "estimated_seconds": 3.0, "visual_keyword": "space ship"},
        {"scene_id": 2, "narration": "Payoff beat 2", "estimated_seconds": 4.0, "visual_keyword": "alien planet"}
    ]

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id="channel_maya",
            topic="Space Odyssey",
            title="Beyond The Stars",
            script="Hook beat 1. Payoff beat 2.",
            scenes_json=json.dumps(test_scenes),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        test_job_id = job.id

    shot_table = mgr.get_shot_table(test_job_id)
    assert shot_table["job_id"] == test_job_id
    assert shot_table["shot_count"] == 2
    assert "Floor Plan" in shot_table["floor_plan"]
    assert len(shot_table["shots"]) == 2


def test_api_get_shot_table_endpoint():
    """Kiểm tra API GET /api/videos/{job_id}/shot-table."""
    client = TestClient(app)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id="channel_maya",
            topic="API Shot Table Test",
            title="API Shot Table Title",
            script="Narration test.",
            scenes_json=json.dumps([{"scene_id": 1, "narration": "Scene 1", "estimated_seconds": 3}]),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        test_job_id = job.id

    res = client.get(f"/api/videos/{test_job_id}/shot-table")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == test_job_id
    assert data["shot_count"] == 1
    assert "shots" in data
    assert data["shots"][0]["shot_id"] == 1


def test_fail_safe_empty_and_corrupted():
    """Kiểm tra khả năng chịu lỗi và tính bền bỉ với đầu vào rỗng hoặc dị biệt."""
    director = SeedanceDirector()

    assert director.enrich_with_physical_anchor("") == ""
    assert director.inject_idle_business("") == ""

    # Dựng shot table với danh sách cảnh rỗng
    empty_table = director.create_shot_table([])
    assert empty_table["shot_count"] == 0
    assert empty_table["shots"] == []
