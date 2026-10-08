import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.database.models import init_db, get_db, VideoJob, User
from app.services.formula_deconstructor import FormulaDeconstructor
from app.services.storyboard_manager import StoryboardManager
from app.schemas.contracts import ScriptContract, SceneContract


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    init_db()
    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 0).first()
        if not u:
            db.add(User(telegram_id=0, username="web_tester", role="admin"))
            db.commit()


def test_formula_deconstructor_extract_blueprint():
    deconstructor = FormulaDeconstructor()
    
    mock_json_blueprint = {
        "title": "Viral Animal Rescue",
        "total_estimated_seconds": 45.0,
        "words_per_minute": 140,
        "hook_analysis": {
            "text": "This dog was left inside an abandoned car for 3 days.",
            "duration_seconds": 3.0,
            "hook_archetype": "Curiosity Gap",
            "retention_trigger": "Deep empathy and curiosity",
            "visual_cue": "Fast zoom on the car"
        },
        "retention_curve": {
            "pacing_style": "Fast & Punchy",
            "pattern_interrupt_interval_seconds": 3.0,
            "tension_peak_second": 30.0,
            "emotional_arc": "Sadness -> Hope -> Joy"
        },
        "beats": [
            {"beat_id": 1, "time_range": "0:00 - 0:03", "duration_seconds": 3.0, "role": "HOOK", "story_function": "Hook audience", "visual_motion_type": "zoom_in"},
            {"beat_id": 2, "time_range": "0:03 - 0:15", "duration_seconds": 12.0, "role": "TENSION", "story_function": "Rescue attempt", "visual_motion_type": "pan_left"},
            {"beat_id": 3, "time_range": "0:15 - 0:45", "duration_seconds": 30.0, "role": "PAYOFF", "story_function": "Happy ending", "visual_motion_type": "cinematic_drift"}
        ],
        "loop_mechanism": {
            "closing_phrase": "Which is why he loves his new family...",
            "connects_to_hook": "Links to beginning"
        },
        "viral_formula_summary": "Animal rescue emotional progression"
    }

    with patch.object(deconstructor.content_agent, "_call_api", return_value=json.dumps(mock_json_blueprint)):
        res = deconstructor.deconstruct_from_source("Sample transcript about a dog rescue", target_lang="en")
        assert res is not None
        assert "blueprint" in res
        bp = res["blueprint"]
        assert bp["title"] == "Viral Animal Rescue"
        assert len(bp["beats"]) == 3
        assert bp["hook_analysis"]["hook_archetype"] == "Curiosity Gap"


def test_formula_deconstructor_generate_script():
    deconstructor = FormulaDeconstructor()
    
    mock_script_dict = {
        "title": "The Golden Retriever That Never Gave Up",
        "hook": "You won't believe what this puppy did to save its brother.",
        "full_narration": "You won't believe what this puppy did to save its brother. He barked until neighbors arrived. That courage saved a life.",
        "scenes": [
            {
                "scene_id": 1,
                "narration": "You won't believe what this puppy did to save its brother.",
                "visual_keyword": "golden retriever puppy",
                "stock_query": "golden retriever puppy",
                "visual_prompt": "Golden retriever puppy looking alert in garden, 8k",
                "estimated_seconds": 3.5,
                "camera_motion": "zoom_in",
                "media_type": "video"
            },
            {
                "scene_id": 2,
                "narration": "He barked until neighbors arrived.",
                "visual_keyword": "puppy barking",
                "stock_query": "dog barking outdoors",
                "visual_prompt": "Puppy barking loudly in backyard, cinematic",
                "estimated_seconds": 4.0,
                "camera_motion": "pan_left",
                "media_type": "video"
            },
            {
                "scene_id": 3,
                "narration": "That courage saved a life.",
                "visual_keyword": "happy dogs running",
                "stock_query": "dogs playing grass",
                "visual_prompt": "Two happy dogs running in grass sunny day",
                "estimated_seconds": 4.0,
                "camera_motion": "cinematic_drift",
                "media_type": "video"
            }
        ],
        "tags": ["shorts", "dogrescue", "viral"],
        "description": "Heartwarming story #shorts #viral",
        "pinned_comment": "Would you have stopped to help?",
        "viral_formula": "Cloned Formula: Empathy Hook",
        "thumbnail_text": "HERO PUPPY"
    }

    bp = {
        "viral_formula_summary": "Empathy Hook",
        "words_per_minute": 150,
        "total_estimated_seconds": 12.0,
        "hook_analysis": {"hook_archetype": "Curiosity Gap", "retention_trigger": "Stops scroll"},
        "beats": [
            {"beat_id": 1, "duration_seconds": 3.5, "role": "HOOK", "story_function": "Scroll stopper", "visual_motion_type": "zoom_in"},
            {"beat_id": 2, "duration_seconds": 4.0, "role": "TENSION", "story_function": "Action", "visual_motion_type": "pan_left"},
            {"beat_id": 3, "duration_seconds": 4.0, "role": "PAYOFF", "story_function": "Resolution", "visual_motion_type": "cinematic_drift"}
        ],
        "loop_mechanism": {"connects_to_hook": "Direct loop"}
    }

    with patch.object(deconstructor.content_agent, "_call_api", return_value=json.dumps(mock_script_dict)):
        script = deconstructor.generate_script_from_blueprint(
            blueprint=bp,
            new_topic="Hero Golden Retriever",
            channel_niche="animal_rescue",
            language="en"
        )
        assert isinstance(script, ScriptContract)
        assert script.title == "The Golden Retriever That Never Gave Up"
        assert len(script.scenes) == 3
        assert script.scenes[0].scene_id == 1


def test_formula_deconstructor_camera_motion_sanitization_and_fallback():
    """Kiểm tra khử lỗi camera_motion dạng LLM descriptive và khả năng fallback không bị lỗi min_acceptable_score."""
    deconstructor = FormulaDeconstructor()

    # Case 1: LLM sinh ra camera_motion tự do không theo regex chuẩn
    raw_llm_json = {
        "title": "Insane 300ft Cliff Leap! 🐆🏔️",
        "hook": "This snow leopard just made the most suicidal jump in nature!",
        "full_narration": "This snow leopard just made the most suicidal jump in nature! Plummeting three hundred feet down a vertical frozen cliff face.",
        "scenes": [
            {
                "scene_id": 1,
                "narration": "This snow leopard just made the most suicidal jump in nature!",
                "visual_keyword": "snow leopard cliff jump",
                "estimated_seconds": 3,
                "camera_motion": "high_energy_video_zoom",
                "media_type": "video"
            },
            {
                "scene_id": 2,
                "narration": "Plummeting three hundred feet down a vertical frozen cliff face.",
                "visual_keyword": "himalayas mountain ibex",
                "estimated_seconds": 9,
                "camera_motion": "dynamic_panning_clip",
                "media_type": "video"
            },
            {
                "scene_id": 3,
                "narration": "At minus forty degrees, one misstep means instant death.",
                "visual_keyword": "frozen himalayas snowstorm",
                "estimated_seconds": 16,
                "camera_motion": "cinematic_broll_sequence",
                "media_type": "video"
            },
            {
                "scene_id": 4,
                "narration": "It's a mid-air collision of pure survival.",
                "visual_keyword": "predator prey clash",
                "estimated_seconds": 3,
                "camera_motion": "dramatic_macro_shot",
                "media_type": "video"
            }
        ],
        "tags": "shorts, viral, wildlife_predators",
        "description": "Watch this insane jump",
        "pinned_comment": "Would you survive?",
        "viral_formula": "Cloned Formula: Life or death hook",
        "thumbnail_text": "300FT CLIFF LEAP!"
    }

    bp = {
        "viral_formula_summary": "Life or death hook",
        "words_per_minute": 165,
        "total_estimated_seconds": 31.0,
        "hook_analysis": {"hook_archetype": "Visual Shock", "retention_trigger": "Stops scroll"},
        "beats": [
            {"beat_id": 1, "duration_seconds": 3.0, "role": "HOOK", "story_function": "Fatal mistake", "visual_motion_type": "high_energy_video_zoom"},
            {"beat_id": 2, "duration_seconds": 9.0, "role": "AGITATION", "story_function": "Extreme environment", "visual_motion_type": "dynamic_panning_clip"},
            {"beat_id": 3, "duration_seconds": 16.0, "role": "CORE_EVIDENCE", "story_function": "Confrontation", "visual_motion_type": "cinematic_broll_sequence"},
            {"beat_id": 4, "duration_seconds": 3.0, "role": "CLIMAX", "story_function": "Clash", "visual_motion_type": "dramatic_macro_shot"}
        ],
        "loop_mechanism": {"connects_to_hook": "Reset hunt"}
    }

    with patch.object(deconstructor.content_agent, "_call_api", return_value=json.dumps(raw_llm_json)):
        script = deconstructor.generate_script_from_blueprint(
            blueprint=bp,
            new_topic="Snow leopard 300-foot vertical cliff jump hunting mountain ibex in frozen Himalayas",
            channel_niche="wildlife_predators",
            language="en"
        )
        assert isinstance(script, ScriptContract)
        assert len(script.scenes) == 4
        # Các motion đã được tự động chuẩn hóa thành enum hợp lệ
        valid_motions = {"zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "hyper_zoom", "warp_zoom", "cinematic_drift"}
        for sc in script.scenes:
            assert sc.camera_motion in valid_motions
        assert isinstance(script.tags, list)
        assert "shorts" in script.tags

    # Case 2: ContentAgent.generate_script không văng TypeError khi nhận min_acceptable_score
    dummy_contract = script
    with patch.object(deconstructor.content_agent, "generate_script", return_value=dummy_contract) as mock_gen:
        # Giả lập LLM văng lỗi format JSON hoàn toàn để kích hoạt nhánh fallback
        with patch.object(deconstructor.content_agent, "_call_api", return_value="INVALID JSON"):
            fallback_script = deconstructor.generate_script_from_blueprint(
                blueprint=bp,
                new_topic="Snow leopard cliff jump",
                channel_niche="wildlife_predators",
                language="en"
            )
            assert fallback_script == dummy_contract
            mock_gen.assert_called_once()


def test_storyboard_manager_lifecycle(tmp_path):
    mgr = StoryboardManager()

    # Tạo một test job trong DB
    test_scenes = [
        {
            "scene_id": 1,
            "narration": "Scene 1 Hook",
            "estimated_seconds": 3.0,
            "visual_keyword": "ocean abyss",
            "stock_query": "deep sea bioluminescence",
            "media_type": "video"
        },
        {
            "scene_id": 2,
            "narration": "Scene 2 Tension",
            "estimated_seconds": 4.0,
            "visual_keyword": "underwater submarine",
            "stock_query": "submarine underwater",
            "media_type": "video"
        }
    ]

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id="channel_maya",
            topic="Deep Ocean Mystery",
            title="What Lurks Below",
            script="Scene 1 Hook. Scene 2 Tension.",
            scenes_json=json.dumps(test_scenes),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    # 1. Test get_storyboard
    sb = mgr.get_storyboard(job_id)
    assert sb["job_id"] == job_id
    assert sb["scene_count"] == 2
    assert sb["scenes"][0]["scene_id"] == 1
    assert sb["scenes"][0]["is_hook"] is True
    assert sb["scenes"][1]["is_hook"] is False

    # 2. Test save_uploaded_media
    dummy_video_bytes = b"FAKE_MP4_HEADER_DATA_1234567890"
    upload_res = mgr.save_uploaded_media(
        job_id=job_id,
        scene_id=1,
        file_bytes=dummy_video_bytes,
        original_filename="user_test.mp4"
    )
    assert upload_res["success"] is True
    assert upload_res["scene_id"] == 1
    assert upload_res["source"] == "user_upload"

    # Verify that get_storyboard now reflects the uploaded media
    sb2 = mgr.get_storyboard(job_id)
    assert sb2["scenes"][0]["has_media"] is True
    assert sb2["scenes"][0]["source"] == "user_upload"


def test_api_storyboard_and_formula_endpoints():
    from fastapi.testclient import TestClient
    from app.web.server import app

    client = TestClient(app)

    # 1. Test Storyboard endpoint for existing job
    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id="channel_maya",
            topic="Test API Storyboard",
            title="API Storyboard Title",
            script="Narration scene 1. Narration scene 2.",
            scenes_json=json.dumps([{"scene_id": 1, "narration": "Scene 1", "estimated_seconds": 3}]),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        test_job_id = job.id

    res = client.get(f"/api/videos/{test_job_id}/storyboard")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == test_job_id
    assert len(data["scenes"]) == 1

    # 2. Test Formula Deconstruct API validation error when empty
    res_empty = client.post("/api/formula/deconstruct", json={"url": ""})
    assert res_empty.status_code == 400

    # 3. Test Formula Deconstruct with mocked deconstructor
    mock_bp = {
        "title": "Mock Deconstructed Short",
        "total_estimated_seconds": 50,
        "words_per_minute": 150,
        "hook_analysis": {"hook_archetype": "Bold Assertion", "text": "Nobody noticed this."},
        "beats": [{"beat_id": 1, "duration_seconds": 3, "role": "HOOK"}],
        "viral_formula_summary": "Bold assertion hook"
    }

    with patch("app.services.formula_deconstructor.FormulaDeconstructor.deconstruct_from_source") as mock_dec:
        mock_dec.return_value = {
            "metadata": {"title": "Viral Short"},
            "transcript_sample": "Sample text",
            "blueprint": mock_bp
        }
        res_dec = client.post("/api/formula/deconstruct", json={"url": "https://youtube.com/shorts/test1234567"})
        assert res_dec.status_code == 200
        assert res_dec.json()["success"] is True
        assert res_dec.json()["blueprint"]["title"] == "Mock Deconstructed Short"

