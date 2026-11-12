"""
Unit & Integration Tests for 9-Channel Sync, Pomodoro Living Studio v2.0, Gaming Lock-In Catalog,
Long-Form Chapter Preservation, Standing Auto-Production Pause Guard, and Smooth Camera Motion Expressions.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from app.agents.publisher_agent import PublisherAgent
from app.database.models import CANONICAL_CHANNEL_MAP, normalize_channel_id
from app.services.autopilot_service import ensure_minimum_channel_queue
from app.services.niche_manager import (
    get_niche,
    validate_channel_niche_alignment,
)
from app.services.pomodoro.scheduler import (
    DEFAULT_CATALOG_BGM_ROOT,
    build_pomodoro_schedule,
    discover_study_library,
)
from app.services.pomodoro.seo_generator import generate_youtube_seo_package
from app.services.pomodoro.thumbnail_generator import generate_youtube_thumbnail
from app.services.pomodoro.mascot_engine import load_mascot_pack, list_available_mascots
from app.services.pomodoro.scene_composer import (
    load_theme_pack,
    list_available_themes,
    select_scene_artwork,
)
from app.services.smart_scheduler import (
    CHANNEL_STAGGER_MINUTES,
    get_next_available_golden_slot,
)
from app.services.video_recycler import resolve_channel_bgm_fast
from app.utils.ffmpeg.color_and_motion import image_to_video
from app.utils.ffmpeg.subtitles_and_thumbnail import validate_output
from config.settings import settings


def test_standing_empty_schedule_pause_remains_active():
    """Đảm bảo lệnh tạm ngưng tự động tạo video khi trống lịch luôn được giữ nguyên."""
    assert settings.PAUSE_EMPTY_SCHEDULE_AUTO_VIDEO is True
    assert settings.ENABLE_OVERDUE_AUTO_PRODUCTION is False
    res = asyncio.run(ensure_minimum_channel_queue(auto_produce=True, notify_telegram=False))
    assert res["paused"] is True
    assert res["produced_count"] == 0


def test_canonical_9_channels_and_music_pool_mapping():
    """Kiểm tra đồng bộ ID chuẩn cho 2 kênh mới trên project_pool_7 và các bí danh kênh."""
    assert normalize_channel_id("UCchbsYVxypzr3CAFI1svyMQ") == "ch_pomodoro_focus_academy"
    assert normalize_channel_id("@PomodoroFocusAcademy") == "ch_pomodoro_focus_academy"
    assert normalize_channel_id("UC83iaowxxjavnGZ107WendQ") == "ch_lock_in_gaming_mix"
    assert normalize_channel_id("@LOCKINGamingMix") == "ch_lock_in_gaming_mix"
    assert CANONICAL_CHANNEL_MAP["ucchbsyvxypzr3cafi1svymq"] == "ch_pomodoro_focus_academy"
    assert CANONICAL_CHANNEL_MAP["uc83iaowxxjavngz107wendq"] == "ch_lock_in_gaming_mix"

    ch_pomo = settings.get_channel_by_id("UCchbsYVxypzr3CAFI1svyMQ")
    ch_game = settings.get_channel_by_id("UC83iaowxxjavnGZ107WendQ")
    assert ch_pomo is not None and ch_pomo["id"] == "ch_pomodoro_focus_academy"
    assert ch_game is not None and ch_game["id"] == "ch_lock_in_gaming_mix"
    assert ch_pomo["niche"] == "study_pomodoro_music"
    assert ch_game["niche"] == "gaming_lockin_music"


def test_o1_audio_catalog_resolves_study_and_gaming_channels():
    """Kiểm tra bảng băm O(1) audio_catalog.json trả về đúng file WAV 48kHz cho cả 2 kênh nhạc."""
    study_wav = resolve_channel_bgm_fast(
        "ch_pomodoro_focus_academy",
        text_context="cozy rainy piano library study session",
    )
    gaming_wav = resolve_channel_bgm_fast(
        "ch_lock_in_gaming_mix",
        text_context="ranked overdrive cyberpunk bass lock in",
    )
    assert study_wav and os.path.exists(study_wav), f"Missing study BGM: {study_wav}"
    assert "06_study_music_lofi" in study_wav.replace("\\", "/")
    assert gaming_wav and os.path.exists(gaming_wav), f"Missing gaming BGM: {gaming_wav}"
    assert "07_gaming_lockin_hud" in gaming_wav.replace("\\", "/")


def test_smart_scheduler_stagger_and_firewall_for_music_channels():
    """Đảm bảo 2 kênh chung project_pool_7 lệch múi giờ đăng và không bị HardNicheFirewall chặn nhầm."""
    assert CHANNEL_STAGGER_MINUTES["ch_pomodoro_focus_academy"] != CHANNEL_STAGGER_MINUTES["ch_lock_in_gaming_mix"]
    slot_pomo = get_next_available_golden_slot("ch_pomodoro_focus_academy", is_long_form=True)
    slot_game = get_next_available_golden_slot("ch_lock_in_gaming_mix", is_long_form=True)
    assert slot_pomo[0] != slot_game[0]

    pomo_niche = get_niche("study_pomodoro_music")
    game_niche = get_niche("gaming_lockin_music")
    assert pomo_niche["id"] == "study_pomodoro_music"
    assert game_niche["id"] == "gaming_lockin_music"

    res_pomo = validate_channel_niche_alignment(
        channel_id="ch_pomodoro_focus_academy",
        title="2-Hour Study With Me | 50/10 Pomodoro Timer & Cozy Sleeping Golden Retriever",
        topic="Pomodoro Study Session",
    )
    assert res_pomo["passed"], f"Pomodoro topic with mascot falsely blocked: {res_pomo}"

    res_game = validate_channel_niche_alignment(
        channel_id="ch_lock_in_gaming_mix",
        title="LOCK IN // 45-Min Cyberpunk Synthwave & Drift Phonk Gaming Mix (No Ads)",
        topic="Gaming Lock In Mix",
    )
    assert res_game["passed"], f"Gaming mix topic falsely blocked: {res_game}"


def test_publisher_category_10_and_longform_chapters_preserved():
    """Kiểm tra Category 10 (Music) và bảo toàn khối Chapters 00:00 / H:MM:SS cho video dài."""
    pub_pomo = PublisherAgent(
        channel_config={
            "id": "ch_pomodoro_focus_academy",
            "name": "Pomodoro Focus Academy",
            "niche": "study_pomodoro_music",
            "language": "en",
            "video_type": "long",
        }
    )
    assert pub_pomo.resolve_locked_category_id() == "10"

    smart_meta = pub_pomo.build_smart_video_tags_and_hashtags(
        title="Study With Me 50/10 Pomodoro Timer",
        topic="Pomodoro Study Music",
        extra_tags=["study with me", "pomodoro timer", "Shorts", "YouTube Shorts"],
        is_long_form=True,
    )
    tags = smart_meta["tags"]
    hashtags = smart_meta["description_hashtags"]
    assert all("shorts" not in t.lower() for t in tags)
    assert "#shorts" not in smart_meta["enriched_title"].lower()
    assert "#documentary" not in hashtags.lower()
    assert "#studywithme" in hashtags.lower()

    raw_desc = (
        "Welcome to Pomodoro Focus Academy!\n\n"
        "⏱️ POMODORO TIMESTAMPS (CHAPTERS):\n"
        "00:00 Prep Time\n"
        "00:16 Study Session 1\n"
        "50:16 Break Time 1\n"
        "1:40:16 Study Session 2\n\n"
        "• Mode: Cozy Lofi\n"
        "• Timer: 50/10 x 4"
    )
    clean_desc = pub_pomo.sanitize_shorts_description(
        raw_desc,
        base_title="Study With Me 50/10 Pomodoro Timer",
        allow_affiliate=False,
        is_long_form=True,
    )
    assert "00:00 Prep Time\n00:16 Study Session 1\n50:16 Break Time 1\n1:40:16 Study Session 2" in clean_desc
    assert "• Mode: Cozy Lofi" in clean_desc


def test_portable_pomodoro_catalog_and_english_seo_localization(tmp_path: Path):
    """Kiểm tra Pomodoro Studio chạy độc lập từ thư mục phẳng `assets/bgm/06_study_music_lofi` và xuất SEO tiếng Anh."""
    mascots = list_available_mascots()
    themes = list_available_themes()
    assert len(mascots) >= 5
    assert len(themes) >= 6

    default_mp = load_mascot_pack("golden_retriever")
    for m_id in mascots:
        mp = load_mascot_pack(m_id)
        assert mp.resolved_bg_path and mp.resolved_bg_path.exists(), f"Missing mascot sleeping artwork: {m_id}"
        assert mp.resolved_waking_bg_path and mp.resolved_waking_bg_path.exists(), f"Missing mascot waking artwork: {m_id}"
    for t_id in themes:
        tp = load_theme_pack(t_id)
        art_p, _ = select_scene_artwork(default_mp, tp, is_break=False, work_dir=tmp_path)
        assert art_p and art_p.exists(), f"Missing resolved theme artwork: {t_id}"
        if t_id != "autumn_cozy":
            assert tp.resolved_bg_path and tp.resolved_bg_path.exists(), f"Missing dedicated seasonal artwork: {t_id}"

    pools = discover_study_library(DEFAULT_CATALOG_BGM_ROOT)
    assert any(k != "06_bell_sfx" and v for k, v in pools.items())

    sched = build_pomodoro_schedule(
        mode="lofi_focus",
        focus_min=0.5,
        break_min=0.25,
        sessions=2,
        prep_sec=5.0,
        bgm_root=DEFAULT_CATALOG_BGM_ROOT,
    )
    assert len(sched) == 5  # Prep + (Focus, Break) x 2

    mp = load_mascot_pack("golden_retriever")
    tp = load_theme_pack("autumn_cozy")
    seo = generate_youtube_seo_package(
        schedule=sched,
        mode="lofi_focus",
        focus_min=25.0,
        break_min=5.0,
        sessions=2,
        out_dir=tmp_path,
        mascot_pack=mp,
        theme_pack=tp,
        speech_lang="en",
    )
    assert "00:00 " in seo["youtube_description"]
    assert "bình luận" not in seo["pinned_comment_ideas"][0].lower()

    thumb_p = generate_youtube_thumbnail(
        out_jpg=tmp_path / "thumb_en.jpg",
        mascot_pack=mp,
        theme_pack=tp,
        focus_min=25.0,
        break_min=5.0,
        sessions=2,
        speech_lang="en",
    )
    assert thumb_p.exists() and thumb_p.stat().st_size > 20_000


def test_supersampled_ken_burns_smooth_motion_expressions(tmp_path: Path):
    """Kiểm tra toàn bộ 7 chế độ camera_motion trong `image_to_video` đều dùng supersampling 2.5x mượt, không sụp đổ biểu thức."""
    from PIL import Image

    img_p = tmp_path / "sample.jpg"
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(img_p)

    captured_filters = {}

    def _fake_run(args, **kwargs):
        in_idx = args.index("-i") + 1
        vf_idx = args.index("-vf") + 1
        with Image.open(args[in_idx]) as supersampled_im:
            captured_filters["input_size"] = supersampled_im.size
        captured_filters["vf"] = args[vf_idx]
        captured_filters["args"] = args

    for motion in ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "hyper_zoom", "cinematic_drift"):
        with patch("app.utils.ffmpeg.color_and_motion.run_ffmpeg_with_gpu_fallback", side_effect=_fake_run):
            image_to_video(
                image_path=str(img_p),
                duration_seconds=4.0,
                output_path=str(tmp_path / f"{motion}.mp4"),
                width=1080,
                height=1920,
                fps=30,
                camera_motion=motion,
            )
        vf = captured_filters["vf"]
        assert captured_filters["input_size"] == (2700, 4800), (
            f"Missing 2.5x supersampled input on {motion}: {captured_filters['input_size']}"
        )
        assert "bt709" in captured_filters["args"], f"Missing BT709_COLOR_ARGS on {motion}"
        assert "zoompan=" in vf and "s=1080x1920:fps=30,setsar=1" in vf
        assert vf.endswith("format=yuv420p")
        if motion in ("pan_left", "pan_right"):
            assert "(on/" in vf and "x='iw/2-(iw/zoom/2)'" not in vf, f"Horizontal pan collapsed on {motion}: {vf}"
        if motion in ("pan_up", "pan_down"):
            assert "(on/" in vf and "y='ih/2-(ih/zoom/2)'" not in vf, f"Vertical pan collapsed on {motion}: {vf}"
