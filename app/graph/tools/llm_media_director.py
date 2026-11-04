"""app.graph.tools.llm_media_director — Bộ não LLM Đạo diễn (`bind_tools`) & LLM Thẩm định phạm vi tìm kiếm (`search_scope`)."""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from config.settings import settings

logger = logging.getLogger(__name__)
from app.graph.tools.tool_schemas import (
    MEDIA_DIRECTOR_TOOLS,
    _clean_yt_search_phrase,
    _extract_subject_anchor_tokens,
    _is_non_english_or_question_title,
    _sanitize_tool_arg,
    build_single_entity_stock_queries,
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
            "visual_strategy": s.get("visual_strategy", ""),
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
- Identify all CANONICAL PROTAGONIST ENTITIES in the story (e.g. both contenders in an "Entity A vs Entity B" or predator-prey story). Resolve all pronouns ("It", "He", "She", "They", "This creature", "Nó", "Họ", "Ele", "Ela") and generic role epithets ("the predator", "the hunter", "its living prey", "the giant", "the bird") back to the exact canonical entity acting in that specific sentence.
- 🚨 CANONICAL ENTITY SPECIFICITY LOCK (ZERO GENERIC HYPERNYM DOWNGRADE & ZERO FLAWED HINT ECHO):
  * Whenever a scene depicts a specific named species or entity (e.g. "Harpy Eagle", "Three-Toed Sloth", "Komodo Dragon", "Colossal Squid"), EVERY search query (`subject_entity`, `youtube_search_query`, `fallback_broad_query`, `primary_stock_query`, `smart_image_query`) MUST keep the **FULL CANONICAL SPECIFIC NAME** (e.g. `"harpy eagle ..."`, `"three toed sloth ..."`). NEVER shorten or downgrade a multi-word species into a generic hypernym like bare `"eagle"`, `"bird"`, `"bird of prey"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`, `"animal"`, or `"creature"`!
  * Treat `visual_keyword` in `Individual Scenes Breakdown` as an untrusted hint: if it contains literal metaphor objects (`"steel clamps"`, `"hydraulic press"`), unresolved epithets (`"predator"`, `"hunter"`, `"prey"`), or generic hypernyms (`"bird"`), IGNORE that flawed hint and derive the query from `narration` + `FULL SCRIPT STORY`.
- Decode metaphors, similes, and abstract concepts into CONCRETE PHYSICAL QUERIES or 2D DOODLE VISUAL METAPHORS following the `abstract_to_physical_query_translations` in the YAML blueprint above:
  * If a split sentence says "Its bite hits like a hydraulic press", "locking like steel clamps", or "A 230-decibel shockwave", NEVER search for factory machinery, steel clamps, or speakers — search for the canonical protagonist's actual physical anatomy/action in its natural environment!
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
     -> SEARCH-ENGINE-NATIVE DUAL-TRACK QUERY ENGINEERING (STRICTLY FOLLOW THE INJECTED `YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY` ABOVE):
        * `subject_entity` (Scene-Accurate): Set `subject_entity` to the exact entity physically acting in that specific scene, OR in a 2-entity duel (`Entity A vs Entity B`), set `"Entity A or Entity B"` so our Vision Auditor accepts valid footage of either contender!
        * `youtube_search_query` (3–5 words, YAML Dual-Track Strategy & Everyday YouTube Creator Vocabulary):
          - For individual body scenes describing ONE contender's weapon, movement, anatomy, mechanism, or habitat: follow the YAML `Single-Entity Body Scene Rule` and `YAML Clean Single-Entity Real-Action Query Examples` above — ONLY search **`[Single Canonical Entity] + [Real Physical Action / Environment]`** with ZERO `"A vs B"` or `"vs"` (avoiding `#edit` comparison videos and AI Veo clips)!
          - For direct clash/encounter scenes (Opening Hook or Climax clash, when permitted by the YAML `Dual-Track Clash vs. Single-Entity Rule`): write a genuine camera-encounter query following `YAML Direct-Encounter / Hook Query Examples`. Our engine searches BOTH `"A vs B"` and single-entity queries in parallel, and automatically Fast-Drops `"A vs B"` immediately if no clean unedited clash footage exists!
          - 🚫 ZERO SIDE-PROP / OBSERVER ROOM / EMPTY SCENERY DRIFT (CRITICAL): When a narration sentence mentions a secondary side-effect, observer, or metaphor to illustrate the scale of the main event (e.g., car engines dying during a volcanic gas disaster, engineers planning in a command room during a weapon test, or empty trees/valleys), NEVER search for the side-prop, office room, or empty scenery (`"car dashboard night"`, `"military engineers planning"`, `"rainforest canopy"`). Always keep `subject_entity` and `youtube_search_query` anchored to a distinct, dramatic visual angle of the **Primary Phenomenon/Entity** promised in the title!
          - 🚫 EVERYDAY YOUTUBE CREATOR VOCABULARY ONLY (BAN ACADEMIC / LATIN / TEXTBOOK JARGON): Real YouTube creators and stock videographers title their uploads with simple, everyday words — NEVER textbook taxonomy or clinical jargon. Translate any scientific/anatomical phrase in the narration into 3–5 everyday words that real videos use in titles (e.g., instead of `"deep ocean benthic crustacean"`, `"apex elapid"`, `"limnic eruption"`, or `"cervical vertebrae"`, write `"deep sea crab underwater"`, `"king cobra strike"`, `"volcanic crater lake gas"`, `"jaguar biting caiman"`).
          - NEVER write literary storytelling sentences that nobody titles a video with, NEVER include `@tool` function names, and NEVER include any marker listed in `YAML Forbidden #Edit / Quiz / AI-Slop Video Markers`.
        * `fallback_broad_query` / `primary_stock_query` / `secondary_stock_query` (2–4 words, Strictly Single-Entity — FORBIDDEN `"vs"`): Follow the YAML `Stock & Fallback Broad Query Rule` and `YAML Canonical Stock/Fallback Query Examples` above (`[Single Canonical Subject Noun] + [Setting/Action]`).
          - FORBIDDEN #1 (NEVER PUT `"vs"` OR `"A vs B"` IN FALLBACK/STOCK QUERIES): Stock libraries index single subjects; `"vs"` returns 0 relevant results!
          - FORBIDDEN #2 (NO GENERIC TEXTURES, SIDE PROPS OR METAPHOR OBJECTS): When a sentence zooms into a body part, mechanism, side effect, or metaphor, NEVER search generic textures, side props, or inanimate metaphor objects — always anchor to the real canonical entity + visible feature!
          - FORBIDDEN #3 (NEVER DROP THE HEAD NOUN): Always keep the core canonical subject noun in everyday English, never regional/descriptive modifiers or academic jargon alone.

2. DECIDE `search_scope` FOR EACH INDIVIDUAL SCENE (`"SCENE_SPECIFIC"` vs `"SCRIPT_OVERALL"`):
   - `"SCENE_SPECIFIC"`: The split sentence (once pronouns are resolved) describes a clear, standalone visual action of the primary subject.
   - `"SCRIPT_OVERALL"`: The split sentence is purely transitional, philosophical, statistical, or mentions a minor distracting side-effect/observer/prop -> set `"SCRIPT_OVERALL"` and search a fresh visual angle of `overall_script_query`!

IMPORTANT: Emit tool calls for ALL {total_scenes} scenes in a single response!"""

    decisions_by_scene: Dict[int, Dict[str, Any]] = {}

    if not (is_long_form_dir and total_scenes >= 14):
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

                import time as _time
                tool_capable_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
                for model_name, api_key in rotator.iter_best_model_key_pairs(tool_capable_models, max_total_attempts=4):
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    gen_cfg_tool: Dict[str, Any] = {"temperature": 0.2}
                    if "lite" not in model_name.lower() and not model_name.startswith("gemma-"):
                        gen_cfg_tool["thinkingConfig"] = {"thinkingBudget": 0}
                    payload["generationConfig"] = gen_cfg_tool
                    t_req0 = _time.monotonic()
                    try:
                        resp = requests.post(url, json=payload, timeout=(3.5, 22 if total_scenes >= 25 else 11.5))
                        if resp.status_code == 200:
                            rotator.record_model_success(api_key, model_name, _time.monotonic() - t_req0)
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
                            rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:800])
                    except Exception as req_err:
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(req_err))
                        logger.debug(f"[LLM Tool Director] {model_name} error: {req_err}")
                        continue
        except Exception as ex:
            logger.warning(f"[LLM Tool Director] bind_tools fallback sang heuristic thông minh: {ex}")
    else:
        logger.info(
            f"⚡ [LLM Tool Director Fast-Pass] Kịch bản Phim Tài Liệu 10 Chương ({total_scenes} cảnh) đã có truy vấn đa nguồn "
            f"độc lập cho từng cảnh -> Kích hoạt điều phối Đa Nguồn 16:9 tức thì!"
        )

    final_plan: List[Dict[str, Any]] = []
    motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    first_en_vk = str((scenes[0].get("visual_keyword") if scenes else "") or "").strip()
    first_en_sq = str((scenes[0].get("stock_query") if scenes else "") or "").strip()
    default_subj = (
        _clean_yt_search_phrase(primary_entity or "", max_words=3, strip_distractor_props=True)
        or _clean_yt_search_phrase(first_en_sq or first_en_vk, max_words=2, strip_distractor_props=True)
        or _clean_yt_search_phrase(topic, max_words=3, strip_distractor_props=True)
    )
    default_overall_q = (
        _clean_yt_search_phrase(topic, subject_entity=default_subj, max_words=5)
        or _clean_yt_search_phrase(first_en_vk or first_en_sq, subject_entity=default_subj, max_words=5)
        or default_subj
    )

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

            if t_chosen == "render_doodle_2d_explainer_scene":
                t_chosen = "fetch_clean_cinematic_portrait_or_broll"
                d["tool_name"] = t_chosen

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
                raw_fb = d.get("fallback_broad_query") or s.get("stock_query") or default_overall_q
                clean_single_fbs = build_single_entity_stock_queries(
                    raw_queries=[raw_fb, s.get("visual_keyword") or ""],
                    subject_entity=str(d.get("subject_entity") or default_subj),
                    topic=topic,
                    max_words=4,
                )
                d["fallback_broad_query"] = clean_single_fbs[0] if clean_single_fbs else raw_fb
            elif t_chosen == "search_pexels_pixabay_stock_video":
                p_raw = d.get("primary_stock_query") or s.get("stock_query") or s.get("visual_keyword") or default_overall_q
                s_raw = d.get("secondary_stock_query") or s.get("stock_query") or default_overall_q
                clean_p_list = build_single_entity_stock_queries(
                    raw_queries=[p_raw, s_raw, s.get("visual_keyword") or ""],
                    subject_entity=str(d.get("subject_entity") or default_subj),
                    topic=topic,
                    max_words=4,
                )
                p_sq = clean_p_list[0] if clean_p_list else p_raw
                s_sq = clean_p_list[1] if len(clean_p_list) > 1 else (clean_p_list[0] if clean_p_list else s_raw)
                d["primary_stock_query"] = p_sq
                d["secondary_stock_query"] = s_sq
                d["youtube_search_query"] = p_sq
                d["fallback_broad_query"] = s_sq

            init_story_qs = [
                q_item for q_item in dict.fromkeys([
                    str(d.get("youtube_search_query") or d.get("human_action_query") or d.get("action_or_concept_query") or d.get("primary_stock_query") or "").strip(),
                    str(d.get("fallback_broad_query") or d.get("secondary_stock_query") or "").strip(),
                    str(s.get("visual_keyword") or "").strip(),
                ]) if q_item
            ]
            if init_story_qs:
                d["storyline_search_queries"] = init_story_qs[:3]

            d["llm_storyline_decomposed"] = True
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

    # Kênh Tâm lý học / Triết học: Dùng 100% Video B-Roll điện ảnh người thật (tuyệt đối không dùng hoạt họa người que / Doodle 2D)
    if str(channel_niche or "").lower() in ("psychology", "satirical_psychology_animation", "doodle", "philosophy"):
        for d_it in final_plan:
            if d_it.get("tool_name") in ("render_doodle_2d_explainer_scene", "search_google_cinematic_image"):
                d_it["tool_name"] = "fetch_clean_cinematic_portrait_or_broll"
                if not d_it.get("human_action_query"):
                    d_it["human_action_query"] = (
                        d_it.get("scene_concept_summary")
                        or d_it.get("smart_image_query")
                        or d_it.get("youtube_search_query")
                        or "person intense eye contact closeup"
                    )

    # Nếu Gemini functionCall không trả về đủ 100% số cảnh (ví dụ chỉ trả về Cảnh 1),
    # chạy ngay bộ giải mã mạch truyện & thẩm định phạm vi bằng JSON Batch LLM (`audit_and_decide_search_scopes_with_llm_sync`)
    # để đảm bảo 100% phân cảnh đều được LLM đọc hiểu toàn kịch bản, giải mã đại từ/ẩn dụ và khóa chặt thực thể!
    if not kwargs.get("skip_scope_audit") and (len(decisions_by_scene) < total_scenes or not is_long_form_dir):
        final_plan = audit_and_decide_search_scopes_with_llm_sync(
            topic=topic,
            scenes=scenes,
            proposed_decisions=final_plan,
            primary_entity=primary_entity,
            channel_niche=channel_niche,
        )

    return final_plan


_ANALOGY_SYNTAX_RE = re.compile(
    r"\b(?:like a|like an|as if|acting like|acts like|basically a|built-in)\b",
    re.IGNORECASE,
)

_GENERIC_ROLE_OR_CLASS_HYPERNYMS = {
    "predator", "predators", "hunter", "hunters", "prey",
    "animal", "animals", "creature", "creatures", "beast", "beasts",
    "bird", "birds", "raptor", "raptors", "mammal", "mammals",
    "reptile", "reptiles", "insect", "insects", "monster", "monsters",
    "giant", "giants", "victim", "victims", "target", "opponent",
    "contender", "attacker", "defender", "steel", "clamp", "clamps",
    "hydraulic", "press", "machine", "trap",
}


def _restore_full_canonical_entity_in_query(
    query: str,
    lead_ent: str,
    max_words: int = 5,
) -> str:
    """
    Khôi phục tên đầy đủ của thực thể gốc (`lead_ent`, ví dụ `'harpy eagle'`, `'three toed sloth'`)
    khi câu truy vấn bị rút gọn thành danh từ chung (`'eagle'`, `'bird'`, `'predator'`, `'hunter'`, `'prey'`)
    hoặc bị dính vật thể ẩn dụ nghĩa đen (`'steel clamps'`).
    """
    q_clean = str(query or "").strip()
    raw_lead = str(lead_ent or "").strip()
    if not q_clean or not raw_lead:
        return q_clean

    q_words = re.findall(r"[a-zA-Z0-9]+", q_clean)
    q_toks_low = {w.lower() for w in q_words if len(w) >= 3}

    # Nếu `lead_ent` chứa 2 thực thể nối bằng 'and / or / vs / ,' (VD: 'harpy eagle and three-toed sloth'),
    # tách thành từng thực thể đơn lẻ để không bao giờ bị cắt cụt ở từ thứ 3 thành 'harpy eagle three'!
    multi_parts = [
        _clean_yt_search_phrase(pt, max_words=3, strip_distractor_props=True)
        for pt in re.split(r"\b(?:vs\.?|versus|and|or)\b|[,/&|+;]", raw_lead, flags=re.IGNORECASE)
        if pt and pt.strip()
    ]
    multi_parts = [p for p in multi_parts if p]
    if len(multi_parts) >= 2:
        matched_part = None
        for pt in multi_parts:
            pt_toks = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", pt)}
            if pt_toks & q_toks_low:
                matched_part = pt
                break
        lead_clean = matched_part or multi_parts[0]
    else:
        lead_clean = _clean_yt_search_phrase(raw_lead, max_words=3, strip_distractor_props=True)

    if not lead_clean:
        return q_clean
    lead_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", lead_clean)]
    if not lead_toks:
        return q_clean

    # Trường hợp 1: Thực thể gồm >= 2 từ (VD: 'harpy eagle', 'komodo dragon', 'colossal squid')
    # và câu truy vấn chỉ chứa từ đuôi chung ('eagle', 'dragon', 'squid') mà đánh rơi từ định danh đầu ('harpy', 'komodo', 'colossal')
    if len(lead_toks) >= 2 and (lead_toks[-1] in q_toks_low) and (lead_toks[0] not in q_toks_low):
        expanded_words: List[str] = []
        replaced = False
        for w in q_words:
            if not replaced and w.lower() == lead_toks[-1]:
                expanded_words.extend(lead_clean.split())
                replaced = True
            elif w.lower() not in _GENERIC_ROLE_OR_CLASS_HYPERNYMS:
                expanded_words.append(w)
        return _clean_yt_search_phrase(" ".join(expanded_words), subject_entity=lead_clean, max_words=max_words)

    # Trường hợp 2: Câu truy vấn hoàn toàn không chứa bất kỳ token nào của `lead_ent`
    # (VD: 'bird in tree branches', 'predator talons wood branch', 'steel clamps tendon grip')
    if not (q_toks_low & set(lead_toks)):
        filtered_action_words = [
            w for w in q_words
            if w.lower() not in _GENERIC_ROLE_OR_CLASS_HYPERNYMS
            and w.lower() not in ("bird", "of", "prey")
        ]
        merged = f"{lead_clean} {' '.join(filtered_action_words)}".strip()
        return _clean_yt_search_phrase(merged, subject_entity=lead_clean, max_words=max_words) or lead_clean

    # Trường hợp 3: Có chứa token thực thể nhưng vẫn dính từ ẩn dụ/vai trò chung ('steel clamps', 'predator', 'hunter')
    if q_toks_low & {"steel", "clamp", "clamps", "hydraulic", "predator", "hunter"}:
        cleaned_words = [
            w for w in q_words
            if w.lower() not in {"steel", "clamp", "clamps", "hydraulic", "predator", "hunter"}
        ]
        return _clean_yt_search_phrase(" ".join(cleaned_words), subject_entity=lead_clean, max_words=max_words) or q_clean

    return q_clean


def _ensure_subject_anchored_decisions(
    decisions: List[Dict[str, Any]],
    primary_entity: Optional[str],
    topic: str,
) -> List[Dict[str, Any]]:
    """
    Cổng bảo vệ gắn kết chủ thể không phụ thuộc chủ đề (Topic-Agnostic Subject Anchor Gate):
    Đảm bảo mọi phân cảnh đều giữ trọn vẹn tên thực thể cụ thể (`lead_ent`) trong cả
    truy vấn chính, truy vấn phụ và truy vấn dự phòng Stock (chống suy thoái về danh từ chung
    như `'bird'`, `'eagle'`, `'predator'`, `'hunter'`, `'prey'` hoặc vật thể ẩn dụ `'steel clamps'`).
    """
    anchor_toks = _extract_subject_anchor_tokens(primary_entity or "", topic or "", "")
    if not anchor_toks:
        return decisions

    single_ents = build_single_entity_stock_queries(
        raw_queries=[str(primary_entity or ""), str(topic or "")],
        subject_entity=str(primary_entity or ""),
        topic=str(topic or ""),
        max_words=3,
    )
    default_lead_ent = single_ents[0] if single_ents else _clean_yt_search_phrase(
        str(primary_entity or topic or ""), max_words=3, strip_distractor_props=True
    )
    default_lead_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", str(default_lead_ent or ""))]

    out_decisions: List[Dict[str, Any]] = []
    for raw_d in decisions:
        if not isinstance(raw_d, dict):
            out_decisions.append(raw_d)
            continue
        item = dict(raw_d)
        t_name = str(item.get("tool_name") or "")
        is_human_broll_tool = t_name in ("fetch_clean_cinematic_portrait_or_broll", "render_doodle_2d_explainer_scene")
        is_llm_decomposed = bool(item.get("llm_storyline_decomposed"))
        scene_subj = str(item.get("subject_entity") or "").strip()
        scene_subj_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", scene_subj)]
        scene_subj_meaningful = [w for w in scene_subj_toks if w not in _GENERIC_ROLE_OR_CLASS_HYPERNYMS]

        # Nếu `scene_subj` chỉ là từ chỉ vai trò/loại chung ('predator', 'hunter', 'bird', 'prey') hoặc rút gọn thiếu định danh đầu ('eagle' thay vì 'harpy eagle')
        if not scene_subj_meaningful:
            lead_ent = default_lead_ent
        elif (
            len(default_lead_toks) >= 2
            and scene_subj_meaningful == [default_lead_toks[-1]]
        ):
            lead_ent = default_lead_ent
        elif (set(scene_subj_meaningful) & anchor_toks) or is_llm_decomposed:
            scene_ents = build_single_entity_stock_queries(
                raw_queries=[scene_subj],
                subject_entity=scene_subj,
                topic=str(topic or ""),
                max_words=3,
            )
            lead_ent = scene_ents[0] if scene_ents else (scene_subj if is_llm_decomposed else default_lead_ent)
        else:
            lead_ent = default_lead_ent

        if lead_ent and not item.get("creature_name") and not is_human_broll_tool:
            item["subject_entity"] = lead_ent
        if not lead_ent or is_human_broll_tool:
            out_decisions.append(item)
            continue

        # Khóa chặt tính đặc thù thực thể (Canonical Entity Specificity Lock) cho mọi câu truy vấn của cảnh
        for q_key in ("youtube_search_query", "primary_stock_query", "action_or_concept_query", "smart_image_query", "secondary_storyline_query"):
            q_val = str(item.get(q_key) or "").strip()
            if not q_val:
                continue
            item[q_key] = _restore_full_canonical_entity_in_query(q_val, lead_ent, max_words=5)

        # Kiểm tra câu truy vấn dự phòng Stock
        for fb_key in ("fallback_broad_query", "secondary_stock_query"):
            fb_val = str(item.get(fb_key) or "").strip()
            if not fb_val:
                continue
            item[fb_key] = _restore_full_canonical_entity_in_query(fb_val, lead_ent, max_words=4)

        # Đảm bảo storyline_search_queries đồng bộ
        prim_q = str(
            item.get("youtube_search_query")
            or item.get("primary_stock_query")
            or item.get("action_or_concept_query")
            or item.get("human_action_query")
            or item.get("smart_image_query")
            or ""
        ).strip()
        sec_q = str(item.get("secondary_storyline_query") or "").strip()
        fb_q = str(item.get("fallback_broad_query") or item.get("secondary_stock_query") or "").strip()
        merged_story_qs = [q_x for q_x in dict.fromkeys([prim_q, sec_q, fb_q]) if q_x]
        if merged_story_qs:
            item["storyline_search_queries"] = merged_story_qs
        item["scope_audited"] = True
        out_decisions.append(item)

    return out_decisions


def audit_and_decide_search_scopes_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    proposed_decisions: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    channel_niche: str = "general",
    **kwargs,
) -> List[Dict[str, Any]]:
    """
    LLM Node Đánh Giá & Phê Duyệt Quyết Định Tìm Kiếm (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    Đọc toàn bộ kịch bản gốc (`full_script_text`) đối chiếu với từng câu văn đã chia nhỏ (`scenes`)
    và bản kế hoạch `proposed_decisions`.
    Với kịch bản dài (`len(scenes) >= 14`), tự động lọc ra các phân cảnh chưa có token neo chủ thể
    hoặc có cấu trúc so sánh/ẩn dụ để LLM thẩm định sâu trong 1 lượt gọi nhanh (~4s), sau đó
    áp dụng cổng bảo vệ gắn kết chủ thể (`_ensure_subject_anchored_decisions`) cho toàn bộ 40 cảnh.
    """
    if not scenes or not proposed_decisions:
        return proposed_decisions

    if (
        not kwargs.get("force_reaudit")
        and all(
            isinstance(d, dict) and d.get("scope_audited") and d.get("llm_storyline_decomposed")
            for d in proposed_decisions
        )
    ):
        return proposed_decisions

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    review_items = []
    dec_map = {int(d.get("scene_id") or 0): dict(d) for d in proposed_decisions if isinstance(d, dict)}
    anchor_toks = _extract_subject_anchor_tokens(primary_entity or "", topic or "", "")
    is_large_batch = len(scenes) >= 14

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        d = dec_map.get(sid, {})
        narr_txt = str(s.get("narration") or "")
        q_cur = (
            d.get("youtube_search_query")
            or d.get("primary_stock_query")
            or d.get("smart_image_query")
            or d.get("action_or_concept_query")
            or d.get("human_action_query")
            or s.get("visual_keyword")
            or ""
        )
        fb_cur = d.get("fallback_broad_query") or d.get("secondary_stock_query") or s.get("stock_query") or ""
        if is_large_batch and anchor_toks:
            q_toks = set(re.findall(r"[a-zA-Z]{3,}", str(q_cur).lower()))
            fb_toks = set(re.findall(r"[a-zA-Z]{3,}", str(fb_cur).lower()))
            needs_llm_audit = (
                not (q_toks & anchor_toks)
                or not (fb_toks & anchor_toks)
                or bool(_ANALOGY_SYNTAX_RE.search(narr_txt))
            )
            if not needs_llm_audit:
                continue

        review_items.append({
            "scene_id": sid,
            "split_sentence": narr_txt,
            "scene_visual_hint": s.get("visual_keyword", ""),
            "proposed_tool": d.get("tool_name", "search_google_cinematic_image"),
            "proposed_scope": d.get("search_scope", "SCENE_SPECIFIC"),
            "proposed_subject_entity": d.get("subject_entity", ""),
            "proposed_query": q_cur,
            "proposed_fallback_stock_query": fb_cur,
            "overall_script_query": d.get("overall_script_query", ""),
        })

    if not review_items:
        return _ensure_subject_anchored_decisions(proposed_decisions, primary_entity, topic)
    if is_large_batch and len(review_items) > 16:
        review_items = review_items[:16]

    from app.services.niche_manager import build_video_search_strategy_prompt_block
    yaml_search_strategy_block = build_video_search_strategy_prompt_block(channel_niche or "general")

    audit_prompt = f"""You are the Chief LLM Script-to-Storyline Search Query Decomposer & Search Scope Judge in a LangGraph video pipeline.
Topic: "{topic}"
Primary Subject: "{primary_entity or topic}"
Channel Niche: "{channel_niche or 'general'}"

{yaml_search_strategy_block}

FULL SCRIPT STORY:
"{full_script_text}"

PROPOSED SCENE DECISIONS TO DECOMPOSE & AUDIT (WARNING: `scene_visual_hint`, `proposed_query`, and `proposed_fallback_stock_query` may contain raw un-decoded metaphors, pronouns, or generic nouns — YOU MUST AUDIT AND OVERWRITE ANY FLAWED DRAFT QUERY!):
{json.dumps(review_items, ensure_ascii=False, indent=2)}

YOUR 3-STEP COGNITIVE STORYLINE DECOMPOSITION PROTOCOL (STRICTLY GOVERNED BY THE YAML VIDEO SEARCH STRATEGY ABOVE):
STEP 1 — FULL-SCRIPT STORYLINE ARC COMPREHENSION, CANONICAL ENTITY LOCK & PRONOUN/METAPHOR RESOLUTION:
- 100% PURE ENGLISH SEARCH QUERIES MANDATE (ZERO PORTUGUESE / VIETNAMESE / QUESTION PREFIXES): Even when `Topic` or `FULL SCRIPT STORY` is in Portuguese (`pt-BR`, e.g. `"Por Que Tubarões..."`, `"Por Que uma Alcateia..."`), Vietnamese (`vi`), or starts with `"Why / How / Por Que / Como"`, EVERY search query (`overall_script_query`, `subject_entity`, `approved_subject_entity`, `approved_query`, `approved_secondary_query`, `approved_fallback_stock_query`) MUST be translated into 100% clean English visual nouns + physical actions! NEVER copy `"Por Que"`, `"Why"`, `"Como"`, `"uma"`, `"do"`, or non-English words into any search query field!
- Read the FULL SCRIPT STORY from Scene 1 to Scene {len(scenes)} first so you understand the complete narrative arc (Act 1: Crisis/Hook -> Act 2: Mechanism/Escalation -> Act 3: Mid-Video Twist -> Act 4: Counter-Move/Climax -> Act 5: Payoff), the exact habitat/era, and the **Canonical Protagonist Entities** (`canonical_protagonists`, e.g., both specific species/entities in a predator-prey or 1v1 story).
- When a `split_sentence` uses pronouns ("It", "He", "She", "They", "This creature", "These witnesses", "This trap"), role epithets ("the predator", "the hunter", "its living prey", "the giant", "the bird"), or omits the subject, resolve it against `FULL SCRIPT STORY` to identify the exact Canonical Entity acting in that sentence.
- 🚨 CANONICAL ENTITY SPECIFICITY LOCK (ZERO GENERIC HYPERNYM DOWNGRADE & ZERO FLAWED HINT ECHO):
  * NEVER shorten or downgrade a specific named entity/species into a generic hypernym or role noun such as bare `"bird"`, `"bird of prey"`, `"raptor"`, `"eagle"`, `"snake"`, `"shark"`, `"whale"`, `"animal"`, `"creature"`, `"beast"`, `"predator"`, `"hunter"`, or `"prey"`!
  * For example, if the canonical protagonist is `"Harpy Eagle"` and the co-protagonist is `"Three-Toed Sloth"`, then every eagle scene MUST explicitly include `"harpy eagle"` in `approved_subject_entity`, `approved_query`, `approved_secondary_query`, AND `approved_fallback_stock_query` (e.g. `"harpy eagle flying rainforest canopy"`, `"harpy eagle talons branch closeup"`, `"harpy eagle carrying prey flight"` — NEVER bare `"eagle flying forest"`, `"bird of prey talons"`, `"predator talons wood branch"`, or `"bird in tree branches"`!), and every sloth scene MUST explicitly include `"three toed sloth"` (or `"sloth"`).
  * DISTRUST FLAWED DRAFT HINTS: If `scene_visual_hint`, `proposed_query`, or `proposed_fallback_stock_query` contains a literal metaphor object (`"steel clamps"`, `"hydraulic press"`), an epithet (`"predator"`, `"hunter"`, `"prey"`), or a generic hypernym (`"bird"`, bare `"eagle"`), DISCARD it completely and write 100% fresh queries naming the exact canonical protagonist!
- 🚫 NEVER USE ABSTRACT THEORY NAMES AS `approved_subject_entity`: A camera cannot film an abstract psychological/philosophical term like `"Bystander Effect"`, `"Diffusion of Responsibility"`, `"Cognitive Dissonance"`, or `"Paradox"`. When the topic is an abstract concept, set `approved_subject_entity` to the **CONCRETE CAMERA-VISIBLE SUBJECT** of that specific scene (e.g., `"witness looking through window"`, `"flashing police lights night"`, `"glowing brain neurons"`, `"man pointing finger"`, `"person calling emergency phone"`).
- 🚫 PRESERVE DRAMATIC STORYLINE MOOD (NO TOURIST VLOG / HAPPY STREET DRIFT): Never flatten a tense/dark storyline moment into a generic casual phrase that returns unrelated tourist vlogs or fast-food queues!
- 🚫 ZERO SIDE-PROP / METAPHOR OBJECT / EMPTY SCENERY DRIFT: When a `split_sentence` uses a metaphor (`"locking like steel clamps"`, `"hits like a freight train"`) or mentions a secondary side-effect, NEVER search for the literal metaphor object (`"steel clamps"`, `"train"`) or empty trees—anchor all 3 queries to the canonical protagonist's real anatomical weapon or physical action!
- 🚫 EVERYDAY YOUTUBE CREATOR VOCABULARY ONLY (BAN ACADEMIC / LATIN / TEXTBOOK JARGON): Translate any clinical/scientific terms (`"benthic crustacean"`, `"apex elapid"`, `"limnic eruption"`, `"diffusion of responsibility"`) into 3–5 everyday English words that real videos use in titles (`"deep sea crab underwater"`, `"king cobra strike"`, `"volcanic crater lake gas"`, `"3d glowing brain neurons"`).

STEP 2 — DECOMPOSE EACH SCENE INTO 3 COMPLEMENTARY STORYLINE SEARCH QUERIES (`approved_query`, `approved_secondary_query`, `approved_fallback_stock_query`):
For EVERY scene (`scene_id = 1..{len(scenes)}`), break that scene's narrative beat down into 3 distinct, storyline-matched search queries that ALL preserve the full canonical entity name:
1. `approved_query` (3–5 English words — Primary YouTube Storyline Query):
   - Captures the #1 most vivid, camera-visible physical action + mood of this exact beat in the script, explicitly naming the full canonical entity acting in this scene.
   - Follow the YAML `Dual-Track Clash vs. Single-Entity Rule`: for single-entity body scenes, use **`[Full Canonical Entity] + [Real Physical Action / Environment]`** with ZERO `"vs"`; keep `"Entity A attacking/vs Entity B"` ONLY on direct 2-entity clash scenes when permitted by the YAML block.
2. `approved_secondary_query` (3–5 English words — Alternate Camera Angle / Reaction Shot for the Same Storyline Beat):
   - A second distinct search query capturing a complementary visual angle or reaction of the SAME scene beat in the story, also explicitly naming the full canonical entity (never downgrading to `"bird"`, `"raptor"`, `"predator"`, or `"hunter"`).
3. `approved_fallback_stock_query` (2–4 English words — Pexels/Pixabay Stock B-Roll Query, ZERO `"vs"`):
   - Concrete, stock-indexable physical subject + action/setting in simple everyday English (`[Full Canonical Subject Noun] + [Action/Setting]`), NEVER shortening a multi-word species to a bare generic noun (e.g. write `"harpy eagle perched branch"`, NEVER `"eagle perched branch"` or `"bird in tree branches"`) and NEVER using `"vs"`.
4. ENFORCE INTRA-VIDEO STORYLINE PROGRESSION:
   - Never assign identical queries to two different scenes; each scene's queries must visually advance the story from beginning to end.

Return ONLY a valid JSON object in this exact format:
{{
  "holistic_script_summary": "1-sentence summary of the overarching script story, habitat/setting, and canonical protagonists",
  "canonical_protagonists": ["Exact Canonical Entity 1", "Exact Canonical Entity 2"],
  "overall_script_query": "3-5 word core visual theme of the story naming the primary canonical entity",
  "subject_entity": "main concrete camera-visible canonical subject(s) of the story",
  "scenes_audit": [
    {{
      "scene_id": 1,
      "resolved_scene_intent": "1-sentence explanation of what physically happens in this scene after resolving all pronouns, epithets, and metaphors",
      "approved_scope": "SCENE_SPECIFIC",
      "approved_subject_entity": "exact full canonical entity acting in this scene (never a generic hypernym like bird/predator/hunter/prey)",
      "approved_query": "3-5 word primary YouTube query with full canonical entity name matching this storyline beat",
      "approved_secondary_query": "3-5 word alternate angle query with full canonical entity name for this same storyline beat",
      "approved_fallback_stock_query": "2-4 word concrete stock video query with full canonical entity name and zero vs",
      "reason": "Storyline-matched visual beat decomposition"
    }}
  ]
}}"""

    try:
        import time as _time
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=4):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                gen_cfg = KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1)
                payload = {
                    "contents": [{"parts": [{"text": audit_prompt}]}],
                    "generationConfig": gen_cfg,
                }
                t_req0 = _time.monotonic()
                try:
                    req_t = 12 if model_name.startswith("gemma-") else 11.5
                    resp = requests.post(url, json=payload, timeout=(3.5, req_t))
                    if resp.status_code == 200:
                        rotator.record_model_success(api_key, model_name, _time.monotonic() - t_req0)
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(
                                p.get("text", "")
                                for p in cands[0].get("content", {}).get("parts", [])
                                if isinstance(p, dict) and not p.get("thought")
                            )
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                parsed = json.loads(m.group(0))
                                holistic_summary = str(parsed.get("holistic_script_summary") or "").strip()
                                canonical_prots = [
                                    str(cp).strip()
                                    for cp in (parsed.get("canonical_protagonists") or [])
                                    if str(cp).strip()
                                ]
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
                                        if holistic_summary:
                                            item["holistic_script_summary"] = holistic_summary
                                        if canonical_prots:
                                            item["canonical_protagonists"] = canonical_prots
                                        if top_overall_q and not _is_non_english_or_question_title(top_overall_q):
                                            item["overall_script_query"] = _clean_yt_search_phrase(top_overall_q, max_words=5) or top_overall_q
                                        if (
                                            top_subj
                                            and not _is_non_english_or_question_title(top_subj)
                                            and not item.get("creature_name")
                                            and (not item.get("subject_entity") or _is_non_english_or_question_title(str(item.get("subject_entity") or "")))
                                        ):
                                            item["subject_entity"] = _clean_yt_search_phrase(top_subj, max_words=3) or top_subj
                                        if sid in audit_by_sid:
                                            au = audit_by_sid[sid]
                                            app_scope = str(au.get("approved_scope") or item.get("search_scope") or "SCENE_SPECIFIC").upper()
                                            app_query = str(au.get("approved_query") or "").strip()
                                            app_sec_query = str(au.get("approved_secondary_query") or "").strip()
                                            app_fb_query = str(au.get("approved_fallback_stock_query") or "").strip()
                                            app_subj_ent = str(au.get("approved_subject_entity") or "").strip()
                                            res_intent = str(au.get("resolved_scene_intent") or "").strip()
                                            item["search_scope"] = app_scope if app_scope in ("SCENE_SPECIFIC", "SCRIPT_OVERALL") else "SCENE_SPECIFIC"
                                            item["scope_audit_reason"] = str(au.get("reason") or "")
                                            if res_intent:
                                                item["resolved_scene_intent"] = res_intent
                                            if app_subj_ent and not item.get("creature_name"):
                                                item["subject_entity"] = app_subj_ent
                                            if app_sec_query:
                                                item["secondary_storyline_query"] = app_sec_query
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
                                            # Chỉ giữ lại các câu truy vấn đã được LLM phê duyệt (không giữ câu raw `visual_keyword` lỗi từ bước trước)
                                            story_qs = [
                                                q_s for q_s in dict.fromkeys([
                                                    app_query,
                                                    app_sec_query,
                                                    app_fb_query,
                                                ]) if q_s
                                            ]
                                            if story_qs:
                                                item["storyline_search_queries"] = story_qs[:4]
                                            item["llm_storyline_decomposed"] = True
                                        item["scope_audited"] = True
                                        audited_list.append(item)

                                    audited_list = _ensure_subject_anchored_decisions(
                                        audited_list, primary_entity, topic
                                    )
                                    verdict_log = ", ".join(
                                        f"S{x.get('scene_id')}:{x.get('tool_name')}[{x.get('search_scope')}]"
                                        for x in audited_list
                                    )
                                    logger.info(
                                        f"⚖️ [LLM Scope Judge Node] Đã thẩm định xong {len(audited_list)} cảnh "
                                        f"qua `{model_name}` -> {verdict_log}"
                                    )
                                    return audited_list
                    elif resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:800])
                except Exception as e_req:
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(e_req)[:180])
                    logger.debug(f"[LLM Scope Judge] {model_name} error: {e_req}")
                    continue
    except Exception as ex:
        logger.debug(f"[LLM Scope Judge] Fallback pass: {ex}")

    return _ensure_subject_anchored_decisions(proposed_decisions, primary_entity, topic)
