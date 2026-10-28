"""app.graph.tools.llm_media_director — Bộ não LLM Đạo diễn (`bind_tools`) & LLM Thẩm định phạm vi tìm kiếm (`search_scope`)."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)
from app.graph.tools.tool_schemas import (
    MEDIA_DIRECTOR_TOOLS,
    _clean_yt_search_phrase,
    _sanitize_tool_arg,
)


# ──────────────────────────────────────────────────────────────────────────────
# BỘ ĐẠO DIỄN LLM BIND_TOOLS (PHÂN TÍCH KỊCH BẢN TỔNG THỂ VS CÂU NHỎ TỰ ĐỘNG)
# ──────────────────────────────────────────────────────────────────────────────

def plan_media_tools_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    channel_niche: str = "cinematic",
    is_long_form: bool = False,
    **kwargs,
) -> List[Dict[str, Any]]:
    """
    Sử dụng Gemini gắn `bind_tools(MEDIA_DIRECTOR_TOOLS)` gồm 4 công cụ chuyên biệt:
      1. `summon_epic_monster_cinematic_media`: Cho các cảnh quái vật khổng lồ, thủy quái biển sâu, khủng long, kaiju, thực thể kinh dị, megalophobia.
      2. `fetch_clean_cinematic_portrait_or_broll`: Cho các cảnh tâm lý học, thao túng tâm lý, khắc kỷ, cảm xúc con người (sạch chữ 100%).
      3. `clone_youtube_short_peak_clip`: Cho các cảnh động vật hoang dã, thiên nhiên, khoa học, đời thực cần cắt Peak YouTube Shorts.
      4. `search_google_cinematic_image`: Cho các cảnh tìm kiếm đa ảnh Top Google Images thông thường.
    """
    total_scenes = len(scenes)
    if total_scenes == 0:
        return []

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    scenes_brief = [
        {
            "scene_id": s.get("scene_id"),
            "narration": s.get("narration", ""),
            "visual_keyword": s.get("visual_keyword", ""),
            "estimated_seconds": s.get("estimated_seconds", 4.0),
        }
        for s in scenes
    ]

    from app.services.niche_manager import build_llm_production_overview_prompt
    niche_yaml_blueprint = build_llm_production_overview_prompt(
        channel_niche or "general",
        include_all_niches_map=True,
    )

    is_long_form_dir = bool(is_long_form) or (total_scenes >= 12)
    longform_chapter_note = (
        "\n- LONG-FORM 16:9 WIDESCREEN DOCUMENTARY MODE ACTIVE (14–24 SCENES ACROSS 5 CHAPTERS):\n"
        "  * Scenes are grouped into Chapter Clusters (4 consecutive scenes per Chapter Master video: Scenes 1-4 = Ch.1, 5-8 = Ch.2, 9-12 = Ch.3, 13-16 = Ch.4, etc.).\n"
        "  * For the first scene of each Chapter Cluster (Scenes 1, 5, 9, 13, 17), write a crisp 3–5 word 16:9 documentary search query (`youtube_search_query` / `action_or_concept_query` / `human_action_query`) that captures the entire Chapter's visual theme so our Chapter-Pool engine downloads ONE rich 16:9 master video and slices multiple non-contiguous angles locally!\n"
        "  * Interleave ~75%–80% real 16:9 documentary video tools (`clone_youtube_short_peak_clip`, `summon_epic_monster_cinematic_media`, `fetch_clean_cinematic_portrait_or_broll`) with ~20%–25% analytical visual metaphors (`render_doodle_2d_explainer_scene` at 1920x1080 for psychology/mechanisms on 1 scene per chapter).\n"
        if is_long_form_dir
        else ""
    )

    prompt = f"""You are an Elite Viral YouTube {'16:9 Documentary & ' if is_long_form_dir else ''}Shorts Visual Director & Holistic Script Reasoning Brain with 6 specialized media production tools.
Topic: "{topic}"
Primary Subject/Entity: "{primary_entity or topic}"
Channel Niche: "{channel_niche}"
Total Scenes: {total_scenes}{longform_chapter_note}

{niche_yaml_blueprint}

FULL SCRIPT STORY:
"{full_script_text}"

Individual Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR 2-STEP COGNITIVE REASONING PROTOCOL (NO BRITTLE KEYWORD MATCHING):
STEP 1 — HOLISTIC SCRIPT COMPREHENSION, PRONOUN RESOLUTION & SUBTEXT DECODING:
- Read the FULL SCRIPT STORY and the YAML NICHE PRODUCTION BLUEPRINT above from start to finish before deciding any tool or search query.
- Resolve all pronouns ("It", "He", "She", "They", "This creature", "Nó", "Họ", "Ele", "Ela") and restore missing subjects in split sentences back to the true protagonist ("{primary_entity or topic}").
- Decode metaphors, similes, and abstract concepts into CONCRETE PHYSICAL QUERIES or 2D DOODLE VISUAL METAPHORS following the `abstract_to_physical_query_translations` in the YAML blueprint above:
  * If a split sentence says "Its bite hits like a hydraulic press" or "A 230-decibel shockwave", NEVER search for factory machinery or speakers — search for the protagonist's actual physical action in its natural environment!
  * If a psychology/behavior sentence discusses abstract manipulation, gaslighting, dopamine traps, or mental cages, translate it using the YAML `abstract_to_physical_query_translations` into either: (a) a real forensic interrogation / 3D glowing brain MRI / iconic character YouTube Short (`clone_youtube_short_peak_clip` or `fetch_clean_cinematic_portrait_or_broll`), OR (b) a 2D Doodle animation (`render_doodle_2d_explainer_scene`).

STEP 2 — SELECT THE OPTIMAL `@tool` & CONTEXT-ANCHORED SEARCH QUERY FOR EACH SCENE (scene_id=1 to {total_scenes}):
1. CHOOSE THE RIGHT SPECIALIZED TOOL BASED ON THE YAML NICHE PRODUCTION BLUEPRINT & DEEP SCRIPT INTENT (ENFORCE 90%–100% REAL MOTION VIDEO ACROSS THE SHORT, INCLUDING 100% MOTION VIDEO ON SCENE 1 AND THE FINAL SCENE):
   - CRITICAL ENDING RETENTION RULE: Scene 1 (Hook), Scene 2, the PENULTIMATE SCENE (scene_id={max(1, total_scenes - 1)}), and the FINAL SCENE (scene_id={total_scenes}) MUST ALWAYS output REAL MOTION VIDEO (`clone_youtube_short_peak_clip`, `fetch_clean_cinematic_portrait_or_broll` with `media_preference="YOUTUBE_FORENSIC_OR_CHARACTER_SHORT"` / `"VIDEO_BROLL"`, `summon_epic_monster_cinematic_media` with `monster_media_mode="3D_CGI_VIDEO"`, or `render_doodle_2d_explainer_scene`). NEVER assign static images to the beginning or end of the video!
   - IF THE SCRIPT OR SCENE IS ABOUT COLOSSAL MONSTERS, DEEP-SEA LEVIATHANS (The Bloop, Kraken, Megalodon, Cthulhu, Gargantuan Leviathan), PREHISTORIC MEGAFAUNA (Titanoboa, Mosasaurus, T-Rex), KAIJU (Godzilla), CRYPTIDS (SCP, Siren Head, Wendigo), OR MEGALOPHOBIA/THALASSOPHOBIA:
     -> ALWAYS call `summon_epic_monster_cinematic_media`!
     -> Provide `creature_name` (e.g., "The Bloop Leviathan", "Megalodon", "Kraken") and `scale_anchor_object` (a human object like "submarine", "battleship", "scuba diver", "skyscraper" to show colossal scale).
     -> Use `monster_media_mode="3D_CGI_VIDEO"` for Scene 1 (Hook), body scenes, AND the Final Scene (`scene_id={total_scenes}`).
   - IF THE SCRIPT OR SCENE IS ABOUT HUMAN PSYCHOLOGY, DARK PSYCHOLOGY, FBI INTERROGATION, BODY LANGUAGE, MANIPULATION, NARCISSISM, GASLIGHTING, STOICISM, NEUROSCIENCE, OR INTERPERSONAL TENSION:
     -> Consult the YAML `visual_archetypes_and_tool_routing` and `abstract_to_physical_query_translations` above to pick the exact million-view production tool for each scene:
        (A) `fetch_clean_cinematic_portrait_or_broll` (with `media_preference="YOUTUBE_FORENSIC_OR_CHARACTER_SHORT"` or `"VIDEO_BROLL"`): For FBI interrogation room body language, polygraph/courtroom micro-expressions, iconic movie character cold stares (Thomas Shelby, Patrick Jane, Homelander), or Dark Noir chiaroscuro eye contact tension. Write `human_action_query` as a concrete 3–6 word physical query (e.g., `"fbi interrogation body language eye contact"`, `"suspect sweating interrogation room camera"`, `"close up eyes dark lighting tension"`).
        (B) `clone_youtube_short_peak_clip`: When the scene describes 3D brain anatomy, amygdala fear response, dopamine neural pathways, sensory deprivation experiments, or viral forensic breakdown clips (e.g., `"3d brain neural network glowing animation"`, `"human eye pupil dilating macro slow motion"`).
        (C) `render_doodle_2d_explainer_scene`: When the scene explains an abstract psychological trap, puppet strings of manipulation, social masks, people-pleasing, or dopamine/money traps that cannot be filmed literally with a camera — or when the channel niche is `satirical_psychology_animation` / `doodle`. Choose `character_tag` (`@MANIPULATOR`, `@PLEASER`, `@MODERNYOU`, `@ANCESTOR`, `@FARMER`) and `prop_type` (`puppet_strings`, `mask`, `cage`, `broken_compass`, `empty_wallet`, `scroll_feed`, `treadmill`, `trophy`).
        (NEVER call `search_google_cinematic_image` for psychology because open-web Google Images return text quote cards and book covers!).
   - FOR REAL-WORLD WILDLIFE, NATURE, ANIMAL RESCUE, SCIENCE, OR ALTERNATE HISTORY TOPICS:
     -> Call `clone_youtube_short_peak_clip` (`clip_role="HOOK"` for Scene 1, and `clip_role="PEAK"` for all body scenes AND the Final Scene `scene_id={total_scenes}`), only calling `search_google_cinematic_image` on a middle scene when a specific historical map, document, or static artifact has no motion video equivalent (and NEVER on Scene 1, Scene 2, Scene {max(1, total_scenes - 1)}, or Scene {total_scenes}).
     -> SEARCH-ENGINE-NATIVE QUERY ENGINEERING (CRITICAL — PREVENT DUMB SEARCHES):
        * `subject_entity` (Scene-Accurate): Set `subject_entity` to the exact entity physically acting in that specific scene, OR in a 2-entity duel (`Entity A vs Entity B`), set `"Entity A or Entity B"` (e.g., `"Leopard or African Crested Porcupine"`) so our Vision Auditor accepts valid footage of either contender!
        * `youtube_search_query` (3–5 words, Real YouTube Titles): Write the exact 3–5 word phrase real YouTube Shorts creators use in video titles (e.g., `"leopard attacking porcupine"`, `"porcupine rattling quills closeup"`, `"leopard injured by porcupine quills"`, `"porcupine defending against leopard"`). NEVER write narrative story phrases that nobody titles a video with (FORBIDDEN: `"porcupine walking away savanna"`, `"porcupine quill embedded in animal skin macro"`).
        * `fallback_broad_query` / `primary_stock_query` / `secondary_stock_query` (2–3 words, Pexels/Pixabay Index Format): Write the 2–3 word **canonical common species/subject noun + habitat/action** that Pexels/Pixabay actually index (e.g., `"porcupine wildlife"`, `"porcupine quills closeup"`, `"leopard hunting savanna"`).
          - FORBIDDEN #1 (NO GENERIC TEXTURES): When a sentence zooms into a body part or microscopic mechanism (`"these needles"`, `"microscopic barbs"`, `"1500 PSI bite"`), NEVER write `"microscopic texture nature"` or `"sharp needle texture macro"` (which returns sewing needles, incense sticks, and leaves on Pexels!). Always anchor to the real animal (`"porcupine quills closeup"`).
          - FORBIDDEN #2 (NEVER DROP THE HEAD NOUN): Always keep the core noun (`"porcupine"`, `"leopard"`, `"octopus"`), never regional adjectives alone (`"African Crested"`).

2. DECIDE `search_scope` FOR EACH INDIVIDUAL SCENE (`"SCENE_SPECIFIC"` vs `"SCRIPT_OVERALL"`):
   - `"SCENE_SPECIFIC"`: The split sentence (once pronouns are resolved) describes a clear, standalone visual action of the subject.
   - `"SCRIPT_OVERALL"`: The split sentence is purely transitional, philosophical, statistical, or mentions a minor distracting metaphor/prop -> set `"SCRIPT_OVERALL"` and search around `overall_script_query`!

IMPORTANT: Emit tool calls for ALL {total_scenes} scenes in a single response!"""

    decisions_by_scene: Dict[int, Dict[str, Any]] = {}

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            func_declarations = []
            for t_obj in MEDIA_DIRECTOR_TOOLS:
                schema = t_obj.args_schema.model_json_schema() if getattr(t_obj, "args_schema", None) else {}
                props = {}
                for k_prop, v_prop in (schema.get("properties") or {}).items():
                    p_type = (v_prop.get("type") or "string").upper()
                    if p_type == "NUMBER":
                        p_type = "NUMBER"
                    elif p_type == "INTEGER":
                        p_type = "INTEGER"
                    else:
                        p_type = "STRING"
                    props[k_prop] = {
                        "type": p_type,
                        "description": v_prop.get("description") or k_prop,
                    }
                req_fields = list(schema.get("required") or ["scene_id"])
                if t_obj.name == "clone_youtube_short_peak_clip":
                    for extra_req in ("overall_script_query", "fallback_broad_query"):
                        if extra_req in props and extra_req not in req_fields:
                            req_fields.append(extra_req)
                elif t_obj.name == "search_pexels_pixabay_stock_video":
                    for extra_req in ("secondary_stock_query", "overall_script_query"):
                        if extra_req in props and extra_req not in req_fields:
                            req_fields.append(extra_req)
                func_declarations.append({
                    "name": t_obj.name,
                    "description": t_obj.description,
                    "parameters": {
                        "type": "OBJECT",
                        "properties": props,
                        "required": req_fields,
                    },
                })

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"function_declarations": func_declarations}],
                "tool_config": {"function_calling_config": {"mode": "ANY"}},
                "generationConfig": {"temperature": 0.2},
            }

            tool_capable_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
            for model_name, api_key in rotator.iter_best_model_key_pairs(tool_capable_models):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, timeout=18)
                    if resp.status_code == 200:
                        data = resp.json()
                        parts = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [])
                        )
                        for p in parts:
                            fc = p.get("functionCall")
                            if fc and isinstance(fc, dict):
                                t_name = fc.get("name", "")
                                t_args = dict(fc.get("args") or {})
                                for str_k in (
                                    "creature_name",
                                    "scale_anchor_object",
                                    "subject_entity",
                                    "action_or_concept_query",
                                    "human_action_query",
                                    "overall_script_query",
                                    "youtube_search_query",
                                    "fallback_broad_query",
                                    "primary_stock_query",
                                    "secondary_stock_query",
                                    "smart_image_query",
                                    "character_tag",
                                    "prop_type",
                                    "scene_concept_summary",
                                ):
                                    if str_k in t_args:
                                        t_args[str_k] = _sanitize_tool_arg(t_args.get(str_k), fallback="")
                                sid = int(t_args.get("scene_id") or 0)
                                if sid > 0:
                                    t_args["tool_name"] = t_name
                                    decisions_by_scene[sid] = t_args
                        if decisions_by_scene:
                            scope_summary = ", ".join(
                                f"S{sid}:{d.get('tool_name')}[{d.get('search_scope', 'SCENE_SPECIFIC')}]"
                                for sid, d in sorted(decisions_by_scene.items())
                            )
                            logger.info(
                                f"🧠 [LLM Tool Director] Đã chọn Tool & Scope cho {len(decisions_by_scene)}/{total_scenes} cảnh "
                                f"qua `{model_name}` ({scope_summary})"
                            )
                            break
                    elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as req_err:
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(req_err))
                    logger.debug(f"[LLM Tool Director] {model_name} error: {req_err}")
                    continue
    except Exception as ex:
        logger.warning(f"[LLM Tool Director] bind_tools fallback sang heuristic thông minh: {ex}")

    final_plan: List[Dict[str, Any]] = []
    motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    default_subj = _clean_yt_search_phrase(primary_entity or topic, max_words=3)
    default_overall_q = _clean_yt_search_phrase(topic, subject_entity=default_subj, max_words=5)

    # Nếu LLM chỉ trả về functionCall cho một số cảnh đầu và bị thiếu các cảnh cuối,
    # lấy công cụ chủ đạo mà LLM đã chọn để bù vào các cảnh bị khuyết (TUYỆT ĐỐI không dùng danh sách từ khóa cứng để ép công cụ).
    llm_monster_ref = next(
        (d for d in decisions_by_scene.values() if d.get("tool_name") == "summon_epic_monster_cinematic_media"),
        None,
    )
    llm_portrait_ref = next(
        (d for d in decisions_by_scene.values() if d.get("tool_name") == "fetch_clean_cinematic_portrait_or_broll"),
        None,
    )
    peak_indices = set(range(1, total_scenes + 1))
    max_video_peak_scenes = total_scenes
    video_peak_count = 0
    mandatory_video_sids = {1, 2, max(1, total_scenes - 1), total_scenes}

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        is_mandatory_video_scene = sid in mandatory_video_sids
        if sid in decisions_by_scene:
            # TÔN TRỌNG QUYẾT ĐỊNH CỦA BỘ NÃO LLM CHO TỪNG PHÂN CẢNH, ĐỒNG THỜI BẢO ĐẢM 100% VIDEO Ở ĐẦU & CUỐI VIDEO (CHỐNG LƯỚT QUA)
            d = dict(decisions_by_scene[sid])
            t_chosen = d.get("tool_name", "")
            d.setdefault("scene_id", sid)
            d.setdefault("subject_entity", default_subj)
            if not d.get("overall_script_query"):
                d["overall_script_query"] = default_overall_q
            if not d.get("search_scope"):
                d["search_scope"] = "SCENE_SPECIFIC"
            if not d.get("camera_motion"):
                d["camera_motion"] = "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)]
            d.setdefault("target_duration_sec", float(s.get("estimated_seconds") or 4.5))

            if is_mandatory_video_scene and t_chosen in ("search_google_cinematic_image", "search_pexels_pixabay_stock_video"):
                if llm_monster_ref is not None:
                    t_chosen = "summon_epic_monster_cinematic_media"
                elif llm_portrait_ref is not None:
                    t_chosen = "fetch_clean_cinematic_portrait_or_broll"
                else:
                    t_chosen = "clone_youtube_short_peak_clip"
                d["tool_name"] = t_chosen
                d.setdefault(
                    "youtube_search_query",
                    d.get("primary_stock_query") or d.get("smart_image_query") or s.get("visual_keyword") or default_overall_q,
                )

            if t_chosen == "summon_epic_monster_cinematic_media":
                clean_c = _sanitize_tool_arg(d.get("creature_name"), fallback=default_subj or topic)
                d["creature_name"] = clean_c
                d["subject_entity"] = _sanitize_tool_arg(d.get("subject_entity"), fallback=clean_c)
                d["scale_anchor_object"] = _sanitize_tool_arg(d.get("scale_anchor_object"), fallback="submarine")
                if is_mandatory_video_scene:
                    d["monster_media_mode"] = "3D_CGI_VIDEO"
                else:
                    d.setdefault("monster_media_mode", "3D_CGI_VIDEO")
            elif t_chosen == "fetch_clean_cinematic_portrait_or_broll":
                d.setdefault("human_action_query", s.get("visual_keyword") or default_overall_q)
                if str(d.get("media_preference", "")).upper() in ("", "CLEAN_PORTRAIT_PHOTO"):
                    d["media_preference"] = "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if (sid % 2 == 1) else "VIDEO_BROLL"
            elif t_chosen == "render_doodle_2d_explainer_scene":
                d.setdefault("character_tag", "@MANIPULATOR")
                d.setdefault("prop_type", "puppet_strings")
                d.setdefault("scene_concept_summary", s.get("visual_keyword") or default_overall_q)
            elif t_chosen == "clone_youtube_short_peak_clip":
                video_peak_count += 1
                d.setdefault("clip_role", "HOOK" if sid == 1 else "PEAK")
                d.setdefault("fallback_broad_query", s.get("stock_query") or default_overall_q)
            elif t_chosen == "search_pexels_pixabay_stock_video":
                p_sq = d.get("primary_stock_query") or s.get("stock_query") or s.get("visual_keyword") or default_overall_q
                s_sq = d.get("secondary_stock_query") or s.get("stock_query") or default_overall_q
                d["primary_stock_query"] = p_sq
                d["secondary_stock_query"] = s_sq
                d["youtube_search_query"] = p_sq
                d["fallback_broad_query"] = s_sq

            d["scope_audited"] = False
            final_plan.append(d)
            continue

        # Chỉ chạy nhánh dự phòng khi LLM không trả về tool call cho phân cảnh `sid` này
        narr = s.get("narration", "")
        kw = s.get("visual_keyword") or narr[:50]
        scope = "SCENE_SPECIFIC" if s.get("visual_keyword") else "SCRIPT_OVERALL"
        clean_kw = _clean_yt_search_phrase(kw, subject_entity=default_subj, max_words=5) or default_overall_q
        dur = float(s.get("estimated_seconds") or 4.5)

        if llm_monster_ref is not None:
            c_name_inh = _sanitize_tool_arg(llm_monster_ref.get("creature_name"), fallback=default_subj or "colossal sea monster")
            s_anchor_inh = _sanitize_tool_arg(llm_monster_ref.get("scale_anchor_object"), fallback="submarine")
            m_mode_inh = "3D_CGI_VIDEO"
            final_plan.append({
                "tool_name": "summon_epic_monster_cinematic_media",
                "scene_id": sid,
                "creature_name": c_name_inh,
                "subject_entity": c_name_inh,
                "scale_anchor_object": s_anchor_inh,
                "monster_media_mode": m_mode_inh,
                "search_scope": scope,
                "action_or_concept_query": clean_kw,
                "overall_script_query": llm_monster_ref.get("overall_script_query") or default_overall_q,
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": False,
            })
        elif llm_portrait_ref is not None:
            m_pref_inh = "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if (sid % 2 == 1) else "VIDEO_BROLL"
            final_plan.append({
                "tool_name": "fetch_clean_cinematic_portrait_or_broll",
                "scene_id": sid,
                "subject_entity": llm_portrait_ref.get("subject_entity") or default_subj,
                "human_action_query": clean_kw,
                "media_preference": m_pref_inh,
                "search_scope": scope,
                "overall_script_query": llm_portrait_ref.get("overall_script_query") or default_overall_q,
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": False,
            })
        elif sid in peak_indices and video_peak_count < max_video_peak_scenes:
            video_peak_count += 1
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "youtube_search_query": clean_kw,
                "overall_script_query": default_overall_q,
                "fallback_broad_query": s.get("stock_query") or default_overall_q,
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": False,
            })
        else:
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "youtube_search_query": clean_kw,
                "overall_script_query": default_overall_q,
                "fallback_broad_query": s.get("stock_query") or default_overall_q,
                "clip_role": "PEAK",
                "target_duration_sec": dur,
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
                "scope_audited": False,
            })

    # Kênh Tâm lý học / Triết học: Cân bằng tỷ lệ vàng giữa Video B-Roll người thật 16:9 (>= 75%) và Hoạt họa 2D Doodle Explainer (~24%: Scene 3, 7, 11, 15)
    if str(channel_niche or "").lower() in ("psychology", "satirical_psychology_animation", "doodle", "philosophy") and total_scenes >= 5:
        doodle_cycle = [
            ("@MANIPULATOR", "puppet_strings"),
            ("@MODERNYOU", "split"),
            ("@PLEASER", "brain_battery"),
            ("@ANCESTOR", "spear_nature"),
        ]
        target_doodle_sids = (
            {s_id for s_id in range(3, total_scenes, 4) if s_id not in (1, total_scenes)}
            if is_long_form_dir
            else {s_id for s_id in (2, 4, 6) if 1 < s_id < total_scenes}
        )
        if is_long_form_dir:
            for idx_d, d_it in enumerate(final_plan):
                s_id_cur = int(d_it.get("scene_id") or (idx_d + 1))
                if s_id_cur in target_doodle_sids:
                    c_tag, p_type = doodle_cycle[(s_id_cur // 4) % len(doodle_cycle)]
                    d_it["tool_name"] = "render_doodle_2d_explainer_scene"
                    d_it["character_tag"] = c_tag
                    d_it["prop_type"] = p_type
                    d_it["scene_concept_summary"] = d_it.get("human_action_query") or d_it.get("youtube_search_query") or default_overall_q
                elif d_it.get("tool_name") == "render_doodle_2d_explainer_scene":
                    d_it["tool_name"] = "fetch_clean_cinematic_portrait_or_broll"
        else:
            doodle_count = sum(1 for d_it in final_plan if d_it.get("tool_name") == "render_doodle_2d_explainer_scene")
            if doodle_count < 2:
                for idx_d, t_sid in enumerate(sorted(target_doodle_sids)):
                    for d_it in final_plan:
                        if int(d_it.get("scene_id") or 0) == t_sid and d_it.get("tool_name") != "render_doodle_2d_explainer_scene":
                            c_tag, p_type = doodle_cycle[idx_d % len(doodle_cycle)]
                            d_it["tool_name"] = "render_doodle_2d_explainer_scene"
                            d_it["character_tag"] = c_tag
                            d_it["prop_type"] = p_type
                            d_it["scene_concept_summary"] = d_it.get("human_action_query") or d_it.get("youtube_search_query") or default_overall_q
                            break
            # Kênh Tâm lý học: Tuyệt đối không dùng ảnh tĩnh MRI/infographic (search_google_cinematic_image),
            # tự động nâng lên Video B-Roll người thật (fetch_clean_cinematic_portrait_or_broll)
            for d_it in final_plan:
                if d_it.get("tool_name") == "search_google_cinematic_image":
                    d_it["tool_name"] = "fetch_clean_cinematic_portrait_or_broll"
                    if not d_it.get("human_action_query"):
                        d_it["human_action_query"] = d_it.get("smart_image_query") or d_it.get("youtube_search_query") or "person intense eye contact closeup"

    return final_plan


def audit_and_decide_search_scopes_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    proposed_decisions: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    """
    LLM Node Đánh Giá & Phê Duyệt Quyết Định Tìm Kiếm (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    Đọc toàn bộ kịch bản gốc (`full_script_text`) đối chiếu với từng câu văn đã chia nhỏ (`scenes`)
    và bản kế hoạch `proposed_decisions`.
    """
    if not scenes or not proposed_decisions:
        return proposed_decisions

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    review_items = []
    dec_map = {int(d.get("scene_id") or 0): dict(d) for d in proposed_decisions if isinstance(d, dict)}

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        d = dec_map.get(sid, {})
        q_cur = (
            d.get("youtube_search_query")
            or d.get("primary_stock_query")
            or d.get("smart_image_query")
            or d.get("action_or_concept_query")
            or d.get("human_action_query")
            or ""
        )
        fb_cur = d.get("fallback_broad_query") or d.get("secondary_stock_query") or ""
        review_items.append({
            "scene_id": sid,
            "split_sentence": s.get("narration", ""),
            "proposed_tool": d.get("tool_name", "search_google_cinematic_image"),
            "proposed_scope": d.get("search_scope", "SCENE_SPECIFIC"),
            "proposed_subject_entity": d.get("subject_entity", ""),
            "proposed_query": q_cur,
            "proposed_fallback_stock_query": fb_cur,
            "overall_script_query": d.get("overall_script_query", ""),
        })

    audit_prompt = f"""You are the Chief LLM Search Scope Judge & Search-Engine Query Auditor in a LangGraph video pipeline.
Topic: "{topic}"
Primary Subject: "{primary_entity or topic}"

FULL SCRIPT STORY:
"{full_script_text}"

PROPOSED SCENE DECISIONS TO AUDIT:
{json.dumps(review_items, ensure_ascii=False, indent=2)}

YOUR 3-STEP COGNITIVE AUDIT PROTOCOL (ELIMINATE ALL DUMB/UNSEARCHABLE QUERIES):
STEP 1 — PRONOUN RESOLUTION & ANATOMY/METAPHOR DECODING ACROSS THE FULL SCRIPT:
- Read the FULL SCRIPT STORY first. When a `split_sentence` uses pronouns ("It", "The cat", "These needles", "This trap", "They") or omits the subject, substitute the true entity physically acting in that sentence.
- When a `split_sentence` describes a microscopic body part, weapon, or metaphor (e.g., "microscopic one-way barbs", "these needles", "like biological harpoons", "1,500 PSI hydraulic press"), NEVER allow generic material/texture queries like `"microscopic texture nature"` or `"sharp needle texture macro"` (which return sewing needles, incense sticks, and leaves on Pexels!). Always rewrite to the actual animal/subject + visible anatomical feature (e.g. `"porcupine quills closeup"`, `"leopard roaring fangs"`).

STEP 2 — AUDIT & REWRITE `approved_query` (FOR YOUTUBE SHORTS) AND `approved_fallback_stock_query` (FOR PEXELS/PIXABAY):
1. `approved_query` (3–5 English words, Real YouTube Shorts Title Format):
   - Must read like a real viral YouTube Shorts title (e.g., `"leopard attacking porcupine"`, `"porcupine rattling quills closeup"`, `"leopard injured by porcupine quills"`, `"porcupine defending against leopard"`).
   - REWRITE immediately if `proposed_query` is a narrative storytelling phrase that nobody titles a video with (e.g., REWRITE `"African crested porcupine walking away savanna"` -> `"crested porcupine quills defense"`; REWRITE `"porcupine quill embedded in animal skin macro"` -> `"porcupine quills in leopard"`).
2. `approved_fallback_stock_query` (2–3 English words, Pexels/Pixabay Canonical Index Format):
   - Must be the **common 1–2 word canonical species/subject noun + habitat/action** that Pexels/Pixabay actually index (e.g., `"porcupine wildlife"`, `"porcupine quills closeup"`, `"leopard hunting savanna"`).
   - NEVER drop the head noun (never output `"African Crested"` without `"Porcupine"`) and NEVER output generic textures (`"microscopic texture"`, `"needle texture"`).
3. `approved_subject_entity` (Scene-Accurate Subject Filter for Vision Auditor):
   - In a 2-entity duel (`Entity A vs Entity B`), set `approved_subject_entity` to the exact entity shown in that scene OR `"Entity A or Entity B"` (e.g., `"Leopard or African Crested Porcupine"`) so our Vision Auditor accepts valid footage of either contender!
4. `NICHE-SPECIFIC ANTI-FLOP GUARDRAILS` (MANDATORY):
   - **Animal Rescue (`animal_rescue` / Paws & Hearts)**: Enforce SINGLE-BREED CONTINUITY across all scenes 1..N! Every scene's `approved_query` and `approved_fallback_stock_query` MUST specify the exact same dog/cat breed and color (e.g. `"white poodle rescue"`, `"golden retriever puppy rescue"`) so the video never stitches 8 different breeds of stray dogs together.
   - **Psychology (`psychology`)**: NEVER output academic infographic or MRI brain scan queries (`"tactical infographic overlay"`, `"neural mapping silhouette"`, `"amygdala brain spike"`). Always rewrite `approved_query` to real human micro-expressions, interrogation, or body language action (`"detective interrogation nervous suspect"`, `"person avoiding eye contact"`, `"confident handshake body language"`).
   - **Alternate History (`alternate_history`)**: NEVER output static portraits or utility-wire queries (`"JP Morgan portrait"`, `"telegraph office"`, `"power lines"`). Always rewrite `approved_query` to high-motion cinematic action footage (`"roman empire siege battle"`, `"mongol cavalry charge"`, `"volcano eruption ash cloud"`, `"tesla coil lightning arc"`).
   - **Intra-Video Uniqueness**: Across all scenes 1..N, NEVER assign the exact same `approved_query` string to two different scenes.

Return ONLY a valid JSON object in this exact format:
{{
  "overall_script_query": "3-5 word core story search query (e.g. 'leopard vs porcupine wildlife')",
  "subject_entity": "main subject(s) of the story (e.g. 'African Crested Porcupine or Leopard')",
  "scenes_audit": [
    {{
      "scene_id": 1,
      "approved_scope": "SCENE_SPECIFIC",
      "approved_subject_entity": "Leopard or African Crested Porcupine",
      "approved_query": "leopard attacking porcupine savanna",
      "approved_fallback_stock_query": "leopard hunting savanna",
      "reason": "Search-engine-native YouTube title query and Pexels canonical fallback"
    }}
  ]
}}"""

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                gen_cfg: Dict[str, Any] = {"temperature": 0.1}
                if not model_name.startswith("gemma-"):
                    gen_cfg["responseMimeType"] = "application/json"
                payload = {
                    "contents": [{"parts": [{"text": audit_prompt}]}],
                    "generationConfig": gen_cfg,
                }
                try:
                    req_t = 14 if model_name.startswith("gemma-") else 18
                    resp = requests.post(url, json=payload, timeout=req_t)
                    if resp.status_code == 200:
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                parsed = json.loads(m.group(0))
                                top_overall_q = str(parsed.get("overall_script_query") or "").strip()
                                top_subj = str(parsed.get("subject_entity") or "").strip()
                                audits = parsed.get("scenes_audit") or []
                                audit_by_sid = {
                                    int(a.get("scene_id") or 0): a
                                    for a in audits
                                    if isinstance(a, dict) and a.get("scene_id")
                                }
                                if audit_by_sid:
                                    audited_list: List[Dict[str, Any]] = []
                                    for d in proposed_decisions:
                                        sid = int(d.get("scene_id") or 0)
                                        item = dict(d)
                                        if top_overall_q:
                                            item["overall_script_query"] = top_overall_q
                                        if top_subj and not item.get("creature_name") and not item.get("subject_entity"):
                                            item["subject_entity"] = top_subj
                                        if sid in audit_by_sid:
                                            au = audit_by_sid[sid]
                                            app_scope = str(au.get("approved_scope") or item.get("search_scope") or "SCENE_SPECIFIC").upper()
                                            app_query = str(au.get("approved_query") or "").strip()
                                            app_fb_query = str(au.get("approved_fallback_stock_query") or "").strip()
                                            app_subj_ent = str(au.get("approved_subject_entity") or "").strip()
                                            item["search_scope"] = app_scope if app_scope in ("SCENE_SPECIFIC", "SCRIPT_OVERALL") else "SCENE_SPECIFIC"
                                            item["scope_audit_reason"] = str(au.get("reason") or "")
                                            if app_subj_ent and not item.get("creature_name"):
                                                item["subject_entity"] = app_subj_ent
                                            if app_fb_query:
                                                item["fallback_broad_query"] = app_fb_query
                                                if item.get("tool_name") == "search_pexels_pixabay_stock_video":
                                                    item["secondary_stock_query"] = app_fb_query
                                            if app_query:
                                                t_n = item.get("tool_name")
                                                if t_n == "clone_youtube_short_peak_clip":
                                                    item["youtube_search_query"] = app_query
                                                elif t_n == "search_pexels_pixabay_stock_video":
                                                    item["primary_stock_query"] = app_query
                                                    item["youtube_search_query"] = app_query
                                                elif t_n == "summon_epic_monster_cinematic_media":
                                                    item["action_or_concept_query"] = app_query
                                                elif t_n == "fetch_clean_cinematic_portrait_or_broll":
                                                    item["human_action_query"] = app_query
                                                else:
                                                    item["smart_image_query"] = app_query
                                        item["scope_audited"] = True
                                        audited_list.append(item)

                                    verdict_log = ", ".join(
                                        f"S{x.get('scene_id')}:{x.get('tool_name')}[{x.get('search_scope')}]"
                                        for x in audited_list
                                    )
                                    logger.info(
                                        f"⚖️ [LLM Scope Judge Node] Đã thẩm định xong {len(audited_list)} cảnh "
                                        f"qua `{model_name}` -> {verdict_log}"
                                    )
                                    return audited_list
                    elif resp.status_code in (429, 403, 404, 503):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as e_req:
                    logger.debug(f"[LLM Scope Judge] {model_name} error: {e_req}")
                    continue
    except Exception as ex:
        logger.debug(f"[LLM Scope Judge] Fallback pass: {ex}")

    return proposed_decisions
