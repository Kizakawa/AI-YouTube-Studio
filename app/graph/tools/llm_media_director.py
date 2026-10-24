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

    prompt = f"""You are an Elite Viral YouTube Shorts Visual Director & Holistic Script Reasoning Brain with 4 specialized media tools.
Topic: "{topic}"
Primary Subject/Entity: "{primary_entity or topic}"
Channel Niche: "{channel_niche}"
Total Scenes: {total_scenes}

FULL SCRIPT STORY:
"{full_script_text}"

Individual Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR 2-STEP COGNITIVE REASONING PROTOCOL (NO BRITTLE KEYWORD MATCHING):
STEP 1 — HOLISTIC SCRIPT COMPREHENSION, PRONOUN RESOLUTION & SUBTEXT DECODING:
- Read the FULL SCRIPT STORY from start to finish before deciding any tool or search query.
- Resolve all pronouns ("It", "He", "She", "They", "This creature", "Nó", "Họ", "Ele", "Ela") and restore missing subjects in split sentences back to the true protagonist ("{primary_entity or topic}").
- Decode metaphors, similes, and abstract statistics into the CONCRETE PHYSICAL REALITY of the protagonist:
  * If a split sentence says "Its bite hits like a hydraulic press" or "A 230-decibel shockwave", NEVER search for factory machinery or speakers — search for the protagonist's actual physical action in its natural environment!
  * Deduce the true natural habitat, historical era, or psychological mood from the full script and keep all scenes visually consistent with that world.

STEP 2 — SELECT THE OPTIMAL TOOL & CONTEXT-ANCHORED SEARCH QUERY FOR EACH SCENE (scene_id=1 to {total_scenes}):
1. CHOOSE THE RIGHT SPECIALIZED TOOL BASED ON DEEP SCRIPT INTENT (VIDEO-FIRST BODY & ENDING CURATION — ENFORCE 90%–100% REAL MOTION VIDEO ACROSS THE SHORT, INCLUDING 100% REAL MOTION VIDEO ON THE FINAL SCENE FOR LOOP-BACK RETENTION):
   - CRITICAL ENDING RETENTION RULE: The FINAL SCENE (scene_id={total_scenes}) and PENULTIMATE SCENE (scene_id={max(1, total_scenes - 1)}) MUST ALWAYS use REAL MOTION VIDEO (`clone_youtube_short_peak_clip`, `media_preference="VIDEO_BROLL"`, or `monster_media_mode="3D_CGI_VIDEO"`). NEVER assign static images to the end of the video, or viewers will immediately swipe away before the loop-back!
   - IF THE SCRIPT OR SCENE IS ABOUT COLOSSAL MONSTERS, DEEP-SEA LEVIATHANS (The Bloop, Kraken, Megalodon, Cthulhu, Gargantuan Leviathan), PREHISTORIC MEGAFAUNA (Titanoboa, Mosasaurus, T-Rex), KAIJU (Godzilla), CRYPTIDS (SCP, Siren Head, Wendigo), OR MEGALOPHOBIA/THALASSOPHOBIA:
     -> ALWAYS call `summon_epic_monster_cinematic_media`!
     -> Provide `creature_name` (e.g., "The Bloop Leviathan", "Megalodon", "Kraken") and `scale_anchor_object` (a human object like "submarine", "battleship", "scuba diver", "skyscraper" to show colossal scale).
     -> Use `monster_media_mode="3D_CGI_VIDEO"` for Scene 1 (Hook), body scenes, AND the Final Scene (`scene_id={total_scenes}`).
   - IF THE SCRIPT OR SCENE IS ABOUT HUMAN PSYCHOLOGY, DARK PSYCHOLOGY, MANIPULATION, NARCISSISM, GASLIGHTING, STOICISM, PHILOSOPHY, OR INTERPERSONAL EMOTIONAL TENSION:
     -> ALWAYS call `fetch_clean_cinematic_portrait_or_broll`! (NEVER call `search_google_cinematic_image` for psychology because open-web Google Images return text quote cards and book covers!).
     -> Write `human_action_query` as a concrete 2-4 word physical human action or facial micro-expression reflecting the scene's emotional subtext (e.g., "close up eyes dark lighting", "couple argument tension", "man stressed alone dark room", "silhouette person walking night").
     -> Use `media_preference="VIDEO_BROLL"` for Scene 1, body scenes, AND the Final Scene (`scene_id={total_scenes}`).
   - FOR REAL-WORLD WILDLIFE, NATURE, SCIENCE, HISTORY, OR DOCUMENTARY TOPICS:
     -> Call `clone_youtube_short_peak_clip` (`clip_role="HOOK"` for Scene 1, and `clip_role="PEAK"` for all body scenes AND the Final Scene `scene_id={total_scenes}`), only calling `search_google_cinematic_image` on a middle scene when a specific historical map, document, or static artifact has no motion video equivalent (and NEVER on Scene 1, Scene 2, Scene {max(1, total_scenes - 1)}, or Scene {total_scenes}).
     -> PEAK VIDEO QUERY PRIMING (CRITICAL): When writing `youtube_search_query` for `clone_youtube_short_peak_clip`, NEVER write passive noun-only queries (e.g., FORBIDDEN: `"black caiman river"`, `"honey badger savanna"`). ALWAYS include the **resolved protagonist + explosive physical action, climax verb, or dramatic phenomenon** (e.g., `"black caiman hunting amazon"`, `"honey badger fearless lion encounter"`, `"boiling river amazon steam"`, `"sperm whale deep dive underwater"`) so our Peak Scanner extracts the most intense action moment!

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
                func_declarations.append({
                    "name": t_obj.name,
                    "description": t_obj.description,
                    "parameters": {
                        "type": "OBJECT",
                        "properties": props,
                        "required": schema.get("required") or ["scene_id"],
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
                                    "overall_script_query",
                                    "youtube_search_query",
                                    "smart_image_query",
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
                if is_mandatory_video_scene:
                    d["media_preference"] = "VIDEO_BROLL"
                else:
                    d.setdefault("media_preference", "VIDEO_BROLL")
            elif t_chosen == "clone_youtube_short_peak_clip":
                video_peak_count += 1
                d.setdefault("clip_role", "HOOK" if sid == 1 else "PEAK")
                d.setdefault("fallback_broad_query", default_overall_q)

            d["scope_audited"] = True
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
                "scope_audited": True,
            })
        elif llm_portrait_ref is not None:
            m_pref_inh = "VIDEO_BROLL"
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
                "scope_audited": True,
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
                "fallback_broad_query": default_overall_q,
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
                "scope_audited": True,
            })
        else:
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "youtube_search_query": clean_kw,
                "overall_script_query": default_overall_q,
                "fallback_broad_query": default_overall_q,
                "clip_role": "PEAK",
                "target_duration_sec": dur,
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
                "scope_audited": True,
            })

    try:
        from app.services.video_memory_rag import get_video_memory_rag
        final_plan = get_video_memory_rag().diversify_intra_video_scene_queries(
            job_id=0,
            scenes_strategy=final_plan,
            topic=topic,
            primary_entity=primary_entity or topic,
            scenes_raw=scenes,
        )
    except Exception:
        pass
    return final_plan


def audit_and_decide_search_scopes_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    proposed_decisions: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
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
            or d.get("smart_image_query")
            or d.get("action_or_concept_query")
            or d.get("human_action_query")
            or ""
        )
        review_items.append({
            "scene_id": sid,
            "split_sentence": s.get("narration", ""),
            "proposed_tool": d.get("tool_name", "search_google_cinematic_image"),
            "proposed_scope": d.get("search_scope", "SCENE_SPECIFIC"),
            "proposed_query": q_cur,
            "overall_script_query": d.get("overall_script_query", ""),
        })

    audit_prompt = f"""You are the Chief LLM Search Scope Judge & Holistic Script Context Auditor in a LangGraph video pipeline.
Topic: "{topic}"
Primary Subject: "{primary_entity or topic}"

FULL SCRIPT STORY:
"{full_script_text}"

PROPOSED SCENE DECISIONS TO AUDIT:
{json.dumps(review_items, ensure_ascii=False, indent=2)}

YOUR 2-STEP COGNITIVE AUDIT PROTOCOL:
STEP 1 — PRONOUN RESOLUTION & SUBTEXT DECODING ACROSS THE FULL SCRIPT:
- Read the FULL SCRIPT STORY first. When a `split_sentence` uses pronouns ("It", "He", "They", "This", "Nó", "Ele") or omits the main subject, mentally substitute the true protagonist ("{primary_entity or topic}") and its real environment from the full script.
- When a `split_sentence` uses a metaphor, simile, or abstract statistic (e.g., "built like a hydraulic press", "faster than a blink", "like a silent cage"), decode what is physically happening to the protagonist rather than searching literal metaphor words.

STEP 2 — APPROVE OR REWRITE `approved_scope` AND `approved_query` FOR EACH SCENE (1 to {len(scenes)}):
1. If `split_sentence` (after pronoun resolution) describes a clear, concrete, visualizable action or state of the main subject:
   - Set `"approved_scope": "SCENE_SPECIFIC"`
   - Ensure `"approved_query"` explicitly includes the resolved subject + concrete physical action/setting (3–5 English words), never a dangling pronoun or literal metaphor prop.
2. If `split_sentence` is purely transitional, abstract, rhetorical, or focuses on a minor inanimate comparison that would hijack search results away from the story:
   - Set `"approved_scope": "SCRIPT_OVERALL"`
   - Rewrite `"approved_query"` so it searches for a fresh, visually compelling angle of the MAIN SCRIPT PROTAGONIST ("{primary_entity or topic}") in its true environment.

Return ONLY a valid JSON object in this exact format:
{{
  "overall_script_query": "3-5 word core story search query",
  "subject_entity": "2-4 word main subject",
  "scenes_audit": [
    {{
      "scene_id": 1,
      "approved_scope": "SCENE_SPECIFIC",
      "approved_query": "stray golden retriever shivering in rain",
      "reason": "Resolved subject and concrete physical action from script context"
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
                                        if top_subj and not item.get("creature_name"):
                                            item["subject_entity"] = top_subj
                                        if sid in audit_by_sid:
                                            au = audit_by_sid[sid]
                                            app_scope = str(au.get("approved_scope") or item.get("search_scope") or "SCENE_SPECIFIC").upper()
                                            app_query = str(au.get("approved_query") or "").strip()
                                            item["search_scope"] = app_scope if app_scope in ("SCENE_SPECIFIC", "SCRIPT_OVERALL") else "SCENE_SPECIFIC"
                                            item["scope_audit_reason"] = str(au.get("reason") or "")
                                            if app_query:
                                                t_n = item.get("tool_name")
                                                if t_n == "clone_youtube_short_peak_clip":
                                                    item["youtube_search_query"] = app_query
                                                elif t_n == "summon_epic_monster_cinematic_media":
                                                    item["action_or_concept_query"] = app_query
                                                elif t_n == "fetch_clean_cinematic_portrait_or_broll":
                                                    item["human_action_query"] = app_query
                                                else:
                                                    item["smart_image_query"] = app_query
                                        item["scope_audited"] = True
                                        audited_list.append(item)

                                    try:
                                        from app.services.video_memory_rag import get_video_memory_rag
                                        audited_list = get_video_memory_rag().diversify_intra_video_scene_queries(
                                            job_id=0,
                                            scenes_strategy=audited_list,
                                            topic=topic,
                                            primary_entity=primary_entity or topic,
                                            scenes_raw=scenes,
                                        )
                                    except Exception:
                                        pass

                                    verdict_log = ", ".join(
                                        f"S{x.get('scene_id')}:{x.get('tool_name')}[{x.get('search_scope')}]"
                                        for x in audited_list
                                    )
                                    logger.info(
                                        f"⚖️ [LLM Scope Judge Node + MiniRAG] Đã thẩm định & đa dạng hóa xong {len(audited_list)} cảnh "
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
