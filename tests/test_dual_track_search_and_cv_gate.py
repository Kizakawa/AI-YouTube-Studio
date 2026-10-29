"""
Unit & Smoke Tests for Dual-Track Video Search ('Search A vs B + Single-Entity, Fast-Drop A vs B if Not Found'),
0ms #edit / Quiz / AI-Slop Metadata Filtering, 16:9 + 9:16 Raw Documentary Unlock, and CV Natural-Texture False-Positive Elimination.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont
from app.graph.tools.media_tools import (
    has_versus_pattern,
    is_edit_or_ai_slop_video_metadata,
    build_single_entity_stock_queries,
    _resolve_adaptive_search_queries,
    _search_youtube_flat_cached,
    _extract_subject_anchor_tokens,
)
from app.services.video_memory_rag import VideoMemoryRAG


def test_dual_track_queries_4_domains():
    print("=== Test 1: 4-Domain Counterfactual Dual-Track Query Resolution ===")
    # Domain 1: Wildlife Matchup (Saltwater Crocodile vs Bull Shark)
    scope, yt_qs = _resolve_adaptive_search_queries(
        search_scope="SCRIPT_OVERALL",
        scene_query="saltwater crocodile vs bull shark",
        overall_script_query="saltwater crocodile vs bull shark",
        fallback_query="",
        subject_entity="Saltwater Crocodile or Bull Shark",
        topic="Saltwater Crocodile vs Bull Shark: 3,700 PSI #Shorts",
        narration="Two apex predators collide where river meets ocean.",
    )
    print("Domain 1 (Wildlife) YT Parallel Queries:", yt_qs)
    assert any(has_versus_pattern(q) for q in yt_qs), "Must keep 'A vs B' branch for YouTube real-encounter discovery!"
    assert any(not has_versus_pattern(q) for q in yt_qs), "Must ALSO include clean single-entity branch in parallel!"

    stock_qs_w = build_single_entity_stock_queries(
        raw_queries=yt_qs,
        subject_entity="Bull Shark",
        topic="Saltwater Crocodile vs Bull Shark: 3,700 PSI",
    )
    print("Domain 1 (Wildlife) Stock Fallback Single-Entity Queries:", stock_qs_w)
    assert all(not has_versus_pattern(q) for q in stock_qs_w), "Stock queries must NEVER contain 'vs'!"
    assert "bull shark" in stock_qs_w[0].lower(), f"Expected Bull Shark prioritized for Bull Shark scene, got {stock_qs_w}"

    # Domain 2: Ancient History (Roman Legion vs Carthaginian War Elephants)
    _, yt_qs_h = _resolve_adaptive_search_queries(
        search_scope="SCENE_SPECIFIC",
        scene_query="roman legion marching formation",
        overall_script_query="roman legion vs carthaginian war elephants",
        fallback_query="roman legion shields",
        subject_entity="Roman Legion",
        topic="Roman Legion vs Carthaginian War Elephants",
        narration="Disciplined cohorts opened lanes for the charging beasts.",
    )
    print("Domain 2 (History) YT Parallel Queries:", yt_qs_h)
    assert yt_qs_h[0] == "roman legion marching formation"
    assert any(has_versus_pattern(q) for q in yt_qs_h)

    # Domain 3: Dark Psychology (Single concrete behavioral query — unaffected by 'vs' logic)
    _, yt_qs_p = _resolve_adaptive_search_queries(
        search_scope="SCENE_SPECIFIC",
        scene_query="fbi interrogation suspect sweating",
        overall_script_query="fbi interrogation body language",
        fallback_query="man intense eye contact",
        subject_entity="FBI Interrogation",
        topic="Why Liars Freeze Their Upper Body",
        narration="Watch the suspect's hands lock under the table.",
    )
    print("Domain 3 (Psychology) YT Parallel Queries:", yt_qs_p)
    assert yt_qs_p[0] == "fbi interrogation suspect sweating"

    # Domain 4: Ocean Mysteries / 3D CGI Monster (Leviathan vs Submarine)
    stock_qs_m = build_single_entity_stock_queries(
        raw_queries=["colossal leviathan vs submarine"],
        subject_entity="Colossal Leviathan",
        topic="Colossal Leviathan vs Submarine: Mariana Trench",
    )
    print("Domain 4 (Ocean Mystery) Single-Entity Queries:", stock_qs_m)
    assert all(not has_versus_pattern(q) for q in stock_qs_m)
    print("Test 1 PASSED!")


def test_0ms_edit_and_ai_slop_filter():
    print("\n=== Test 2: 0ms #edit Comparison & AI Veo Slop Metadata Filter ===")
    # Must BLOCK #edit / quiz / AI Veo spam
    bad_titles = [
        ("Bull shark vs saltwater crocodile #edit #shorts", "WildEdits", False),
        ("Who Won: Bull Shark or Saltwater Crocodile? #shorts", "AnimalQuiz", False),
        ("Who would win? Bull Shark vs Saltwater Crocodile", "DebateHub", False),
        ("Crocodile vs Shark fight #veo #aivideo", "AIVideos", False),
        ("Saltwater Crocodile AI generated video", "VeoCreator", False),
        ("T-Rex vs Spinosaurus Animal Revolt Battle Simulator", "SimChannel", False),
    ]
    for t, ch, cgi in bad_titles:
        assert is_edit_or_ai_slop_video_metadata(t, ch, allow_3d_cgi_monster=cgi), f"Failed to block spam: {t}"

    # Must ALLOW genuine real encounters & safe word boundaries ('caiman', '#airforce')
    good_titles = [
        ("Saltwater crocodile swimming in murky river Australia", "OutbackRaw", False),
        ("Jaguar vs Caiman real fight caught on camera Pantanal", "BrazilWildlife", False),
        ("Bull shark underwater close up Bahamas", "OceanDive4K", False),
        ("Black caiman hunting in Amazon river #wildlife", "AmazonDoc", False),
        ("Fighter jet takeoff #airforce", "AviationHD", False),
        ("Gargantuan Leviathan 3D animation megalophobia", "OceanVFX", True),
    ]
    for t, ch, cgi in good_titles:
        assert not is_edit_or_ai_slop_video_metadata(t, ch, allow_3d_cgi_monster=cgi), f"Falsely blocked clean video: {t}"
    print("Test 2 PASSED!")


def test_cv_natural_ripples_vs_real_captions():
    print("\n=== Test 3: CV Artifact Detector (0% False Positive on Dark Ripples/Scales vs 100% True Caption Catch) ===")
    import math
    rng = random.Random(42)

    # 3A. Synthetic dark underwater crocodile/shark frame (organic curved body + 45% dark water + bright scales/ripples)
    im_water = Image.new("RGB", (540, 960))
    px = im_water.load()
    for y in range(960):
        curve = int(95 * math.sin(y / 110.0))
        for x in range(540):
            dist = abs((x - 270 - curve) * 0.9) + abs((y - 480) * 0.45)
            if dist > 235:
                # Natural dark murky river water with subtle sensor grain & blue-green tint
                base = rng.randint(5, 21)
                px[x, y] = (base, min(255, base + rng.randint(3, 10)), min(255, base + rng.randint(5, 14)))
            else:
                # High-frequency organic scales & water glints across curved subject/water
                ripple = 215 if ((x * 7 + y * 13 + rng.randint(0, 19)) % 21 == 0) else rng.randint(18, 95)
                px[x, y] = (max(0, ripple - 18), ripple, min(255, ripple + 12))

    flagged_water, reason_water = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(im_water)
    print(f"Natural dark water + scales/ripples -> flagged={flagged_water}, reason='{reason_water}'")
    assert not flagged_water, f"False positive on natural dark water ripples: {reason_water}"

    # 3B. Synthetic burned-in TikTok/Shorts caption frame (white text with black outline at bottom)
    im_cap = im_water.copy()
    draw = ImageDraw.Draw(im_cap)
    try:
        font = ImageFont.truetype("arialbd.ttf", 46)
    except Exception:
        font = ImageFont.load_default()
    txt = "WHO WOULD WIN THIS FIGHT?"
    for dx in (-3, -2, 0, 2, 3):
        for dy in (-3, -2, 0, 2, 3):
            if dx or dy:
                draw.text((34 + dx, 780 + dy), txt, font=font, fill=(0, 0, 0))
    draw.text((34, 780), txt, font=font, fill=(255, 255, 255))

    flagged_cap, reason_cap = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(im_cap)
    print(f"Burned-in outlined caption -> flagged={flagged_cap}, reason='{reason_cap}'")
    assert flagged_cap, "Must detect real burned-in white-on-black-outline caption banner!"

    # 3C. Digital black quote poster
    im_poster = Image.new("RGB", (540, 960), (0, 0, 0))
    d_post = ImageDraw.Draw(im_poster)
    for idx_line, line_s in enumerate(["10 DARK PSYCHOLOGY TRICKS", "THEY NEVER TEACH YOU", "NUMBER ONE: SILENCE", "WATCH THEIR EYES"]):
        d_post.text((40, 220 + idx_line * 110), line_s, font=font, fill=(255, 255, 255))
    flagged_post, reason_post = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(im_poster)
    print(f"Digital black quote poster -> flagged={flagged_post}, reason='{reason_post}'")
    assert flagged_post, "Must detect digital black quote poster!"
    print("Test 3 PASSED!")


def test_live_dual_track_youtube_search_mix():
    print("\n=== Test 4: Live Dual-Track YouTube Search (9:16 + 16:9 Raw Footage & Spam Filter) ===")
    phrases = ["saltwater crocodile vs bull shark", "saltwater crocodile swimming river", "bull shark underwater"]
    anchors = _extract_subject_anchor_tokens("Saltwater Crocodile or Bull Shark", "Saltwater Crocodile vs Bull Shark", phrases[0])
    toks = {w for p in phrases for w in p.split() if len(w) >= 3}
    cands = _search_youtube_flat_cached(phrases, anchors, toks, allow_3d_cgi_monster=False)
    print(f"Fetched {len(cands)} clean YouTube candidates across Dual-Track queries.")
    if cands:
        n_shorts = sum(1 for c in cands if c.get("is_vertical_short"))
        n_land = sum(1 for c in cands if not c.get("is_vertical_short"))
        print(f"Format mix: {n_shorts} vertical 9:16 Shorts + {n_land} standard 16:9 raw videos")
        print("Top 5 candidates:", [(c["title"][:55], "9:16" if c["is_vertical_short"] else "16:9", round(c["score"], 1)) for c in cands[:5]])
        for c in cands:
            assert not is_edit_or_ai_slop_video_metadata(c["title"], c.get("uploader", "")), f"Spam leaked: {c['title']}"
    print("Test 4 PASSED!")


if __name__ == "__main__":
    test_dual_track_queries_4_domains()
    test_0ms_edit_and_ai_slop_filter()
    test_cv_natural_ripples_vs_real_captions()
    test_live_dual_track_youtube_search_mix()
    print("\nALL 4 DUAL-TRACK SEARCH & CV GATE TESTS PASSED 100%!")
