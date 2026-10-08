"""
Test Suite for Cast & Setting Registry and Nano-Pipeline Integration (inspired by LocalMiniDrama & Seedance 2.0).
Verifies:
  1. Historical & Archetype Cast Anchor Extraction (Nikola Tesla, Einstein, Caesar, Hacker, Executive, etc.)
  2. Setting Anchor Detection (Deep space, ocean abyss, subterranean lab, cyberpunk, etc.)
  3. Niche-based Setting Anchor Fallback
  4. Dialogue Quotes vs Narration Separation (Straight quotes and typographic quotes)
  5. Prompt Binding Idempotency (prevent duplicate anchors)
  6. Shot Table Cast & Dialogue Enrichment
  7. ImageAgent master_cinematic_prompt & generate_scene_image Integration
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app.services.media.seedance_director import (
    SeedanceDirector,
    extract_dialogue_quotes,
)
from app.agents.image_agent import (
    master_cinematic_prompt,
    ImageAgent,
)


def test_cast_anchor_extraction_historical():
    """Kiểm tra nhận diện và gán neo nhân vật chính xác cho các nhân vật lịch sử."""
    director = SeedanceDirector()

    # 1. Nikola Tesla
    res_tesla = director.extract_cast_and_scene_anchors(
        topic="Bí mật cuộc đời Nikola Tesla và tháp Wardenclyffe",
        script_or_scenes="Tesla miệt mài làm việc trong phòng thí nghiệm",
        niche_id="historical_figures"
    )
    assert res_tesla["character_name"] == "Nikola Tesla"
    assert "tall lean 40-year-old inventor" in res_tesla["character_traits"]
    assert "neat mustache" in res_tesla["character_anchor"]

    # 2. Albert Einstein
    res_einstein = director.extract_cast_and_scene_anchors(
        topic="Thuyết tương đối của Albert Einstein",
        script_or_scenes="Einstein nhìn lên bầu trời suy ngẫm",
    )
    assert res_einstein["character_name"] == "Albert Einstein"
    assert "wild iconic gray hair" in res_einstein["character_traits"]

    # 3. Julius Caesar
    res_caesar = director.extract_cast_and_scene_anchors(
        topic="Đế chế La Mã và sự sụp đổ của Julius Caesar",
        script_or_scenes="Caesar bước vào viện nguyên lão",
    )
    assert res_caesar["character_name"] == "Julius Caesar"
    assert "laurel wreath" in res_caesar["character_traits"]


def test_cast_anchor_extraction_archetypes():
    """Kiểm tra nhận diện và gán neo nhân vật cho các hình mẫu nghề nghiệp / xã hội."""
    director = SeedanceDirector()

    # 1. Thám tử (Detective)
    res_det = director.extract_cast_and_scene_anchors(
        topic="Vụ án mạng trong đêm mưa",
        script_or_scenes="Viên thám tử kiểm tra dấu vết hiện trường",
    )
    assert res_det["character_name"] == "The Detective"
    assert "trench coat" in res_det["character_traits"]

    # 2. Hacker / Cyberpunk (The Netrunner)
    res_hack = director.extract_cast_and_scene_anchors(
        topic="Cuộc tấn công mạng toàn cầu",
        script_or_scenes="Hacker gõ phím liên tục trong bóng tối",
    )
    assert res_hack["character_name"] == "The Netrunner"
    assert "cyberpunk hacker" in res_hack["character_traits"]

    # 3. Tổng giám đốc / Sếp (Executive)
    res_exec = director.extract_cast_and_scene_anchors(
        topic="Chiến lược kinh doanh của vị giám đốc",
        script_or_scenes="Chủ tịch bước vào phòng họp hội đồng quản trị",
    )
    assert res_exec["character_name"] == "The Executive"
    assert "tailored suit" in res_exec["character_traits"]

    # 4. Cô gái trẻ (Young Woman)
    res_girl = director.extract_cast_and_scene_anchors(
        topic="Hành trình khám phá cuộc sống",
        script_or_scenes="Cô gái trẻ bước đi trên con phố mùa thu",
    )
    assert res_girl["character_name"] == "Young Woman"


def test_cast_anchor_fallback():
    """Kiểm tra fallback mượt mà khi chủ đề không trùng archetype nào."""
    director = SeedanceDirector()

    res = director.extract_cast_and_scene_anchors(
        topic="Hệ sinh thái san hô biển sâu",
        script_or_scenes="Những rạn san hô kỳ ảo dưới đáy biển",
    )
    assert res["character_name"] != ""
    assert "character_anchor" in res
    assert len(res["character_anchor"]) > 10


def test_setting_anchor_extraction():
    """Kiểm tra nhận diện bối cảnh không gian cố định từ từ khóa trong kịch bản."""
    director = SeedanceDirector()

    # 1. Không gian vũ trụ
    res_space = director.extract_cast_and_scene_anchors(
        topic="Bí ẩn hố đen vũ trụ",
        script_or_scenes="Phi thuyền tiến sâu vào tâm hố đen",
    )
    assert res_space["setting_name"] == "Deep Space Cockpit"
    assert "curved thick viewport" in res_space["setting_traits"]

    # 2. Biển sâu đại dương
    res_ocean = director.extract_cast_and_scene_anchors(
        topic="Vực thẳm Mariana dưới đáy đại dương",
        script_or_scenes="Tàu ngầm lặn xuống đáy rãnh biển sâu",
    )
    assert res_ocean["setting_name"] == "Submerged Research Pod"
    assert "titanium submersible cabin" in res_ocean["setting_traits"]

    # 3. Phòng thí nghiệm
    res_lab = director.extract_cast_and_scene_anchors(
        topic="Nghiên cứu nguyên tử",
        script_or_scenes="Các nhà khoa học trong phòng thí nghiệm",
    )
    assert res_lab["setting_name"] == "Subterranean Laboratory"
    assert "brass and copper pipes" in res_lab["setting_traits"]

    # 4. Cyberpunk
    res_cyber = director.extract_cast_and_scene_anchors(
        topic="Thành phố tương lai năm 2099",
        script_or_scenes="Ánh đèn neon trên con phố mưa cyberpunk",
    )
    assert res_cyber["setting_name"] == "Neo-Metropolis Alleyway"
    assert "holographic billboards" in res_cyber["setting_traits"]


def test_setting_anchor_fallback_by_niche():
    """Kiểm tra bối cảnh tự thích ứng theo Niche ID khi không có từ khóa bối cảnh rõ ràng."""
    director = SeedanceDirector()

    # Niche deep_sea
    res_deep = director.extract_cast_and_scene_anchors(
        topic="Khám phá sinh vật lạ",
        script_or_scenes="Sinh vật phát sáng kỳ lạ",
        niche_id="deep_sea_mysteries"
    )
    assert "Abyssal" in res_deep["setting_name"]

    # Niche wildlife
    res_wild = director.extract_cast_and_scene_anchors(
        topic="Cuộc đi săn của đàn sói",
        script_or_scenes="Đàn sói di chuyển trong tuyết",
        niche_id="wildlife_predators"
    )
    assert "Sanctuary" in res_wild["setting_name"] or "Habitat" in res_wild["setting_name"]


def test_dialogue_quotes_separation():
    """Kiểm tra phân tách lời thoại nhân vật trong ngoặc kép và lời dẫn chuyện người thuyết minh."""
    # 1. Ngoặc kép chuẩn
    text1 = 'Nhìn vào màn hình, nhà khoa học thốt lên: "Chúng ta đã tìm thấy tín hiệu!" sau nhiều năm chờ đợi.'
    parsed1 = extract_dialogue_quotes(text1)
    assert parsed1["is_dialogue"] is True
    assert parsed1["dialogue"] == "Chúng ta đã tìm thấy tín hiệu!"
    assert "Nhìn vào màn hình" in parsed1["narration"]
    assert "sau nhiều năm chờ đợi" in parsed1["narration"]
    assert '"' not in parsed1["narration"]

    # 2. Ngoặc kép cong tiếng Việt (“...”)
    text2 = 'Marcus Aurelius nhìn xa xăm và nói: “Hãy chấp nhận những gì số phận mang lại.”'
    parsed2 = extract_dialogue_quotes(text2)
    assert parsed2["is_dialogue"] is True
    assert "Hãy chấp nhận những gì số phận mang lại." in parsed2["dialogue"]

    # 3. Lời bình thuyết minh thuần túy không có thoại
    text3 = "Vũ trụ bao la chứa hàng tỷ thiên hà, mỗi thiên hà là một điều kỳ diệu."
    parsed3 = extract_dialogue_quotes(text3)
    assert parsed3["is_dialogue"] is False
    assert parsed3["dialogue"] == ""
    assert parsed3["narration"] == text3


def test_bind_cast_to_prompt_idempotent():
    """Kiểm tra gắn neo nhân vật và bối cảnh vào prompt đảm bảo tính bất biến (không nhân bản)."""
    director = SeedanceDirector()

    raw = "Cinematic shot of a man working on a machine"
    char_anchor = "Nikola Tesla: tall lean gentleman with dark mustache, dark suit"
    set_anchor = "Subterranean Lab: exposed brass pipes, amber lamps"

    # Gắn lần 1
    bound1 = director.bind_cast_to_prompt(
        raw,
        character_anchor=char_anchor,
        setting_anchor=set_anchor
    )
    assert "cast: Nikola Tesla:" in bound1
    assert "setting: Subterranean Lab:" in bound1

    # Gắn lần 2 (không được lặp lại 'cast:' hoặc 'setting:')
    bound2 = director.bind_cast_to_prompt(
        bound1,
        character_anchor=char_anchor,
        setting_anchor=set_anchor
    )
    assert bound2.count("cast:") == 1
    assert bound2.count("setting:") == 1
    assert bound2 == bound1


def test_create_shot_table_with_cast_registry():
    """Kiểm tra create_shot_table tạo Bảng phân cảnh chuẩn Seedance 2.0 & LocalMiniDrama."""
    director = SeedanceDirector()

    scenes = [
        {
            "scene_id": 1,
            "narration": 'Thám tử bước vào căn phòng tối và thì thầm: "Hắn ta đã từng ở đây."',
            "visual_keyword": "detective examining fingerprints on broken glass",
            "estimated_seconds": 3.8
        },
        {
            "scene_id": 2,
            "narration": "Những dấu vết máu kéo dài ra phía cửa sổ đang mở toang đón gió mưa.",
            "visual_keyword": "blood trail leading to open rain-soaked window",
            "estimated_seconds": 4.0
        }
    ]

    shot_table = director.create_shot_table(
        scenes=scenes,
        topic="Vụ án bí ẩn trong đêm mưa",
        channel_style="noir"
    )

    assert "Floor Plan & Spatial Orientation" in shot_table["floor_plan"]
    assert shot_table["shot_count"] == 2
    assert "character_anchor" in shot_table
    assert "setting_anchor" in shot_table

    # Kiểm tra phân tích thoại cho Scene 1
    shot1 = shot_table["shots"][0]
    assert shot1["is_dialogue"] is True
    assert shot1["dialogue_text"] == "Hắn ta đã từng ở đây."
    assert shot1["character_anchor"] == shot_table["character_anchor"]
    assert "Lock Line:" in shot1["lock_line"]

    # Kiểm tra Scene 2 không có thoại
    shot2 = shot_table["shots"][1]
    assert shot2["is_dialogue"] is False
    assert shot2["dialogue_text"] == ""


def test_image_agent_master_cinematic_prompt_cast_binding():
    """Kiểm tra master_cinematic_prompt trong ImageAgent nhận và khóa chặt Cast & Setting."""
    char_anchor = "Albert Einstein: wild white hair, tweed jacket"
    set_anchor = "Study: chalkboard with equations, warm lamp"

    prompt = master_cinematic_prompt(
        raw_prompt="Deep thoughtful portrait looking up at the sky",
        scene_id=1,
        niche_id="historical_figures",
        character_anchor=char_anchor,
        setting_anchor=set_anchor,
    )

    assert "cast: Albert Einstein:" in prompt
    assert "setting: Study:" in prompt


def test_image_agent_generate_scene_image_auto_cast(tmp_path):
    """Kiểm tra ImageAgent tự động kích hoạt Cast & Setting Registry khi tạo ảnh."""
    agent = ImageAgent()
    out_file = str(tmp_path / "test_cast_img.jpg")

    captured_prompt = {}

    def fake_pollinations_get(url, headers=None, timeout=None):
        captured_prompt["url"] = url
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.content = b"\xFF\xD8\xFF\xE0" + b"\x00" * 6000  # Fake valid JPEG > 5KB
        return mock_res

    with patch("requests.get", side_effect=fake_pollinations_get), \
         patch.object(agent, "_try_google_image_api", return_value=None), \
         patch.object(agent, "_try_huggingface_image_api", return_value=None), \
         patch.object(agent, "_sanitize_ai_image", return_value=None):

        res = agent.generate_scene_image(
            prompt="Working on high voltage transformer coils in lab",
            output_path=out_file,
            topic="Nikola Tesla and the secrets of free energy",
            scene_id=1,
            niche="historical_figures"
        )

        assert res == out_file
        assert Path(out_file).exists()
        # Verify URL sent to Pollinations encoded the cast anchor
        assert "url" in captured_prompt
        # Tesla should be in the encoded prompt
        assert "Tesla" in captured_prompt["url"]
