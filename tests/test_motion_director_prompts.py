"""
tests/test_motion_director_prompts.py — Kiểm thử Bộ 5 Prompt Đạo Diễn Chuyển Động & Khóa Chất Lượng Video AI:
1. Thổi hồn vào ảnh tĩnh (Micro-movements of hair, fabric, lighting, background)
2. Viết prompt chuyển động siêu mượt (Camera motion directives + subject focus)
3. Biến 1 ảnh thành 3 kiểu video khác nhau (Luxury Commercial / Natural Documentary / Cinematic Emotional)
4. Đạo diễn chuyển động như máy ảnh 100 củ (Image-aware saliency camera director + optical rack focus)
5. Khóa chặt chất lượng, tránh video giả (Anti-morphing, zero facial distortion, stable background geometry)
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw

from app.agents.ai_video_agent import AIVideoAgent
from app.agents.content_agent import ContentAgent


def test_prompt_1_2_5_cinematic_motion_and_anti_morph_lock():
    prompt = AIVideoAgent.build_cinematic_motion_prompt(
        prompt="A lone deep sea diver shining a beam into a bioluminescent trench",
        niche="deep_sea_earth_mysteries",
        camera_motion="dolly_zoom",
        scene_id=1,
        narration="The abyss stared back with a blinding pulse.",
    )
    assert "9:16 vertical video" in prompt
    assert "RED V-Raptor 8K" in prompt
    assert "dolly-zoom" in prompt
    assert "STRICT QUALITY & PHYSICS LOCK" in prompt
    assert "zero facial morphing" in prompt
    assert "natural micro-movements of hair, fabric, and light" in prompt


def test_prompt_3_three_video_style_variants_and_niche_routing():
    variants = AIVideoAgent.build_three_video_style_variants(
        prompt="A rescued golden retriever puppy trembling in the rain",
        camera_motion="zoom_in",
        narration="He waited three days in the pouring rain.",
    )
    assert set(variants.keys()) == {
        "luxury_commercial_imax",
        "natural_documentary",
        "cinematic_emotional",
    }
    assert "RED V-Raptor 8K" in variants["luxury_commercial_imax"]
    assert "Sony FX9 85mm telephoto" in variants["natural_documentary"]
    assert "Arri Alexa 65 anamorphic" in variants["cinematic_emotional"]

    assert AIVideoAgent.resolve_director_style(niche="animal_rescue") == "natural_documentary"
    assert AIVideoAgent.resolve_director_style(niche="psychology") == "cinematic_emotional"
    assert AIVideoAgent.resolve_director_style(niche="deep_sea_earth_mysteries") == "luxury_commercial_imax"


def test_prompt_1_and_4_direct_camera_motion_from_image(tmp_path: Path):
    # 1. Ảnh có chủ thể sắc nét tập trung ở chính giữa + câu thoại cảm xúc/bí mật -> dolly_zoom
    img_center = Image.new("L", (180, 320), color=0)
    draw_c = ImageDraw.Draw(img_center)
    for r in range(55, 85, 6):
        draw_c.rectangle([r, 100, 180 - r, 220], outline=255, width=2)
    p_center = tmp_path / "center.jpg"
    img_center.save(str(p_center))

    m_center = AIVideoAgent.direct_camera_motion_from_image(
        image_path=str(p_center),
        fallback_motion="pan_left",
        scene_id=2,
        narration="Her eyes hid a dark secret in complete silence.",
    )
    assert m_center == "dolly_zoom"

    # 2. Ảnh có chi tiết dồn sang bên phải -> pan_right
    img_right = Image.new("L", (180, 320), color=0)
    draw_r = ImageDraw.Draw(img_right)
    for y in range(40, 280, 12):
        draw_r.line([(115, y), (175, y + 6)], fill=255, width=2)
    p_right = tmp_path / "right.jpg"
    img_right.save(str(p_right))

    m_right = AIVideoAgent.direct_camera_motion_from_image(
        image_path=str(p_right),
        fallback_motion="zoom_in",
        scene_id=3,
        narration="The predator moved through the tall grass.",
    )
    assert m_right == "pan_right"


def test_content_agent_scene_motion_and_micro_physics_enrichment():
    ca = ContentAgent()
    raw = {
        "title": "Why Narcissists Freeze When You Go Silent",
        "hook": "Total silence shatters a manipulator's mask.",
        "scenes": [
            {
                "scene_id": 1,
                "narration": "Total silence shatters a manipulator's mask.",
                "visual_keyword": "intense eyes closeup",
                "visual_prompt": "Close up of a calm person looking ahead",
            },
            {
                "scene_id": 2,
                "narration": "Watch their eyes panic when you reveal nothing.",
                "visual_keyword": "nervous gaze",
                "visual_prompt": "A sharp portrait in dim lighting",
            },
            {
                "scene_id": 3,
                "narration": "Across the vast ocean of human behavior, control is an illusion.",
                "visual_keyword": "dark ocean horizon",
                "visual_prompt": "Wide dark ocean waves under moonlight",
            },
        ],
    }
    parsed = ca._parse_json_script(json.dumps(raw), topic="Dark Psychology", channel_niche="psychology")
    assert parsed.scenes[0].camera_motion == "hyper_zoom"
    assert parsed.scenes[1].camera_motion == "zoom_in"
    assert parsed.scenes[2].camera_motion == "zoom_out"
    assert "natural micro-physics of hair, fabric, and ambient light" in parsed.scenes[1].visual_prompt
    assert "exact facial and anatomical fidelity" in parsed.scenes[1].visual_prompt
