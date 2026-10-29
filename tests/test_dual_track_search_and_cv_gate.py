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


def test_all_14_yaml_niches_video_search_strategy():
    print("\n=== Test 5: All 14 YAML Profiles Single-Source-of-Truth Dual-Track Search Verification ===")
    from app.services.niche_manager import (
        load_all_niches,
        get_niche_video_search_strategy,
        build_script_prompt_instructions,
        build_llm_production_overview_prompt,
    )

    profiles = load_all_niches(force_reload=True)
    assert len(profiles) >= 14, f"Expected all 14 YAML niche profiles, got {len(profiles)}"

    required_vss_keys = (
        "dual_track_clash_rule",
        "single_entity_body_scene_rule",
        "stock_and_fallback_rule",
        "format_unlock_rule",
        "anti_flop_scene_continuity_rule",
        "single_entity_query_examples",
        "stock_fallback_examples",
        "forbidden_edit_markers",
    )
    for prof in profiles:
        nid = prof["id"]
        raw_vss = (prof.get("llm_production_blueprint") or {}).get("video_search_strategy") or {}
        assert raw_vss, f"YAML '{nid}.yaml' is missing llm_production_blueprint.video_search_strategy!"
        for rk in required_vss_keys:
            assert raw_vss.get(rk), f"YAML '{nid}.yaml' missing video_search_strategy.{rk}!"
        merged_vss = get_niche_video_search_strategy(nid)
        assert merged_vss["single_entity_query_examples"], f"Empty single_entity_query_examples for {nid}"
        assert all(not has_versus_pattern(q) for q in merged_vss["stock_fallback_examples"]), (
            f"YAML '{nid}.yaml' stock_fallback_examples must NEVER contain 'vs': {merged_vss['stock_fallback_examples']}"
        )

    # Verify dynamic prompt injection across 4 distinct domains
    for test_niche in ("wildlife_predators", "deep_sea_earth_mysteries", "psychology", "alternate_history"):
        script_prompt = build_script_prompt_instructions(test_niche, language="en")
        director_prompt = build_llm_production_overview_prompt(test_niche, include_all_niches_map=False)
        header = f"YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY (FROM `config/niches/{test_niche}.yaml`)"
        assert header in script_prompt, f"Script prompt missing YAML search strategy for {test_niche}"
        assert header in director_prompt, f"Director prompt missing YAML search strategy for {test_niche}"

    # Verify YAML-configured niche markers dynamically block spam in is_edit_or_ai_slop_video_metadata
    assert is_edit_or_ai_slop_video_metadata(
        "Roman Empire vs Mongol Empire #countryballs #shorts",
        "HistoryMap",
        channel_niche="alternate_history",
    ), "YAML marker #countryballs in alternate_history.yaml must be dynamically blocked!"
    assert is_edit_or_ai_slop_video_metadata(
        "Thomas Shelby cold stare #sigmaedit",
        "MindsetClips",
        channel_niche="psychology",
    ), "YAML marker #sigmaedit in psychology.yaml must be dynamically blocked!"
    assert is_edit_or_ai_slop_video_metadata(
        "Marcus Aurelius wisdom #stoicedit",
        "StoicDaily",
        channel_niche="philosophy_stoicism",
    ), "YAML marker #stoicedit in philosophy_stoicism.yaml must be dynamically blocked!"

    # Verify zero hardcoded crocodile/bull-shark strings remain in Python orchestration prompt files
    repo_root = Path(__file__).resolve().parent.parent
    python_prompt_files = [
        repo_root / "app" / "graph" / "tools" / "llm_media_director.py",
        repo_root / "app" / "graph" / "tools" / "tool_schemas.py",
        repo_root / "app" / "graph" / "nodes" / "hook_video_node.py",
        repo_root / "app" / "agents" / "content" / "script_generator_mixin.py",
    ]
    for py_file in python_prompt_files:
        txt = py_file.read_text(encoding="utf-8").lower()
        assert "saltwater crocodile" not in txt and "bull shark" not in txt, (
            f"Hardcoded wildlife example leaked in Python file {py_file.name} instead of living in YAML!"
        )
    print(f"Verified all {len(profiles)} YAML profiles + dynamic prompt injection + zero Python domain hardcoding! Test 5 PASSED!")


def test_stock_fetcher_no_name_error_and_hook_candidate_dedup():
    print("\n=== Test 6: StockFetcher.search_best_video_clip ('re' import) & Hook Candidate Dedup ===")
    import tempfile
    from unittest.mock import patch
    from app.services.media.stock_fetcher import StockFetcher
    from app.graph.nodes.media_nodes import (
        _extract_hook_candidate_vid_id,
        _collect_active_scene_source_paths,
    )

    sf = StockFetcher(api_key="dummy_test_key")
    with patch("app.graph.parallel_search.run_langgraph_5way_stock_video_search", return_value=[]) as mock_5way, \
         patch.object(StockFetcher, "search_wikimedia_videos", return_value=[]):
        res = sf.search_best_video_clip(
            query="honey badger vs african lions",
            narration="When lions clamp down, the badger twists inside its own hide.",
            topic="Honey Badger vs African Lions",
            visual_analysis={"search_queries": ["honey badger vs lions", "honey badger attacking"]},
            _establishing_pass=True,
        )
        assert res is None
        called_qs = mock_5way.call_args.kwargs.get("candidate_queries") or []
        print("StockFetcher split candidate_queries:", called_qs)
        assert called_qs, "candidate_queries must be populated without NameError!"
        assert all(not has_versus_pattern(q) for q in called_qs), f"'vs' leaked into StockFetcher queries: {called_qs}"

    with tempfile.TemporaryDirectory() as tmpdir:
        hc = Path(tmpdir)
        raw_s2 = hc / "yt_hook_cand_2_LmFxy9yXb34.mp4"
        clean_legacy_s2 = hc / "hook_peak_clean_yt_2_0.mp4"
        clean_new_s2 = hc / "hook_peak_clean_yt_2_0_LmFxy9yXb34.mp4"
        raw_s2.write_bytes(b"0" * 128)
        clean_legacy_s2.write_bytes(b"0" * 128)
        clean_new_s2.write_bytes(b"0" * 128)

        assert _extract_hook_candidate_vid_id(clean_new_s2) == "LmFxy9yXb34"
        assert _extract_hook_candidate_vid_id(clean_legacy_s2) == "LmFxy9yXb34"
        assert _extract_hook_candidate_vid_id(raw_s2) == "LmFxy9yXb34"

        scenes_media = [
            {
                "scene_id": 2,
                "media_path": str(hc / "hook_motion_002.mp4"),
                "full_peak_path": str(clean_legacy_s2),
                "youtube_id": "LmFxy9yXb34",
            }
        ]
        used_paths, used_vids = _collect_active_scene_source_paths(scenes_media, exclude_scene_ids={5})
        assert str(clean_legacy_s2.resolve()) in used_paths
        assert "LmFxy9yXb34" in used_vids
    print("Test 6 PASSED!")


if __name__ == "__main__":
    test_dual_track_queries_4_domains()
    test_0ms_edit_and_ai_slop_filter()
    test_cv_natural_ripples_vs_real_captions()
    test_live_dual_track_youtube_search_mix()
    test_all_14_yaml_niches_video_search_strategy()
    test_stock_fetcher_no_name_error_and_hook_candidate_dedup()
    print("\nALL 6 YAML DUAL-TRACK SEARCH, STOCK FETCHER & DEDUP TESTS PASSED 100%!")
