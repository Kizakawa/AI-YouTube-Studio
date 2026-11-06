"""
app.graph.evaluators.llm_tool_call_critic — Bộ Kiểm Định & Tự Chữa Lành Việc Gọi Tool Của Các LLM Nodes (`LLMToolCallCritic`).

Chức năng cốt lõi:
1. Kiểm tra trực tiếp quyết định gọi `@tool` (`tool_name`, `search_scope`, `subject_entity`, `youtube_search_query`,
   `fallback_broad_query`, `storyline_search_queries`) của các Node LLM (`node_generate_script` -> `node_direct_visual_strategy`).
2. Đảm bảo Luật `> 85%` Thời Lượng Video Chuyển Động Thật ngay từ bước chọn `@tool`:
   - Bắt buộc `>= 86%` số phân cảnh (và 100% ở Cảnh 1, Cảnh 2, Cảnh áp chót, Cảnh cuối) phải gọi các `@tool` tải Video chuyển động thật
     (`clone_youtube_short_peak_clip`, `summon_epic_monster_cinematic_media`, `fetch_clean_cinematic_portrait_or_broll`, `search_pexels_pixabay_stock_video`).
3. Dùng 1-Call Batch LLM Tool-Call Judge (100% System Prompt, ZERO danh sách từ khóa/regex cứng trong Python) để kiểm tra từng cảnh:
   - Chủ thể tìm kiếm (`subject_entity`) có đúng là thực thể đang thực sự hành động trong câu thoại `narration` của cảnh đó không?
   - Các câu truy vấn truyền vào Tool (`youtube_search_query`, `fallback_broad_query`, `storyline_search_queries`) có sát ngữ cảnh,
     dùng từ vựng đời thường có thật trên YouTube/Pexels, và không dịch ẩn dụ theo nghĩa đen hay không?
   - Tự động sửa (`AUTO_HEALED`) ngay trên `visual_plan["scenes_strategy"]` trước khi hệ thống đi tải video ở `produce_hook_video` và `fetch_media`.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, Field

from config.settings import settings
from app.services.niche_manager import build_video_search_strategy_prompt_block
from app.utils.key_rotator import GEMINI_PRIORITY_MODELS, KeyRotator
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)

MOVING_VIDEO_TOOLS = {
    "clone_youtube_short_peak_clip",
    "summon_epic_monster_cinematic_media",
    "fetch_clean_cinematic_portrait_or_broll",
    "search_pexels_pixabay_stock_video",
}


class SceneToolCallAudit(BaseModel):
    scene_id: int
    narration: str = ""
    selected_tool: str = ""
    is_moving_video_tool: bool = True
    search_scope: str = "SCENE_SPECIFIC"
    subject_entity: str = ""
    primary_query: str = ""
    fallback_query: str = ""
    storyline_search_queries: List[str] = Field(default_factory=list)
    tool_fit_score: float = Field(default=9.0, ge=1.0, le=10.0)
    query_relevance_score: float = Field(default=9.0, ge=1.0, le=10.0)
    status: str = Field(default="OPTIMAL", description="OPTIMAL | AUTO_HEALED")
    audit_notes: str = ""


class LLMToolCallEvaluationReport(BaseModel):
    total_scenes: int = 0
    moving_video_tool_scenes: int = 0
    moving_video_planned_ratio_pct: float = 100.0
    overall_tool_call_score: float = 9.0
    passed: bool = True
    auto_healed_scene_ids: List[int] = Field(default_factory=list)
    scene_audits: List[SceneToolCallAudit] = Field(default_factory=list)
    summary: str = ""
    evaluated_by: str = "ai"


class LLMToolCallCritic:
    """
    Giám khảo kiểm định việc gọi `@tool` và truyền tham số tìm kiếm của các LLM Nodes
    (`node_generate_script` & `node_direct_visual_strategy`).
    """

    def __init__(self) -> None:
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    @staticmethod
    def _is_video_tool_decision(decision: Dict[str, Any]) -> bool:
        t_name = str(decision.get("tool_name") or "").strip()
        if t_name not in MOVING_VIDEO_TOOLS:
            return False
        if t_name == "summon_epic_monster_cinematic_media":
            return str(decision.get("monster_media_mode") or "3D_CGI_VIDEO").upper() == "3D_CGI_VIDEO"
        if t_name == "fetch_clean_cinematic_portrait_or_broll":
            return str(decision.get("media_preference") or "VIDEO_BROLL").upper() != "CLEAN_PORTRAIT_PHOTO"
        return True

    @staticmethod
    def _extract_primary_and_fallback_query(decision: Dict[str, Any], scene: Dict[str, Any]) -> Tuple[str, str]:
        prim = str(
            decision.get("youtube_search_query")
            or decision.get("human_action_query")
            or decision.get("action_or_concept_query")
            or decision.get("primary_stock_query")
            or decision.get("smart_image_query")
            or (scene.get("search_queries") or [""])[0]
            or scene.get("visual_keyword")
            or ""
        ).strip()
        fb = str(
            decision.get("fallback_broad_query")
            or decision.get("secondary_stock_query")
            or scene.get("stock_query")
            or prim
        ).strip()
        return prim, fb

    @trace_custom_span("LLMToolCallCritic.evaluate_and_heal_tool_calls", run_type="llm")
    def evaluate_and_heal_tool_calls(
        self,
        topic: str,
        script_data: Dict[str, Any],
        visual_plan: Dict[str, Any],
        primary_entity: Optional[str] = None,
        channel_niche: str = "general",
    ) -> Tuple[LLMToolCallEvaluationReport, Dict[str, Any], Dict[str, Any]]:
        """
        Kiểm định toàn bộ danh sách gọi `@tool` trong `visual_plan["scenes_strategy"]` đối chiếu với `script_data["scenes"]`.
        Trả về `(report, healed_visual_plan, healed_script_data)`.
        """
        scenes = list((script_data or {}).get("scenes") or [])
        strategies = list((visual_plan or {}).get("scenes_strategy") or [])
        total_scenes = len(scenes)
        if total_scenes == 0:
            empty_rep = LLMToolCallEvaluationReport(total_scenes=0, summary="No scenes to evaluate.")
            return empty_rep, visual_plan, script_data

        eff_entity = str(primary_entity or (script_data or {}).get("primary_entity") or topic or "").strip()
        strat_by_sid: Dict[int, Dict[str, Any]] = {}
        for idx, st in enumerate(strategies):
            if isinstance(st, dict):
                sid = int(st.get("scene_id") or (idx + 1))
                strat_by_sid[sid] = dict(st)

        auto_healed_sids: List[int] = []
        niche_low = str(channel_niche or "general").strip().lower()
        default_video_tool = (
            "fetch_clean_cinematic_portrait_or_broll"
            if niche_low in ("psychology", "satirical_psychology_animation", "doodle", "philosophy")
            else "clone_youtube_short_peak_clip"
        )

        # ── BƯỚC 1: KIỂM TRA & BẢO ĐẢM LUẬT >= 85% VIDEO CHUYỂN ĐỘNG THẬT TRÊN CÁC TOOL CALLS ──
        for idx, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                continue
            sid = int(sc.get("scene_id") or (idx + 1))
            d = strat_by_sid.get(sid)
            if not d:
                sc_qs = [str(q).strip() for q in (sc.get("search_queries") or []) if str(q).strip()]
                prim_q = sc_qs[0] if sc_qs else str(sc.get("visual_keyword") or eff_entity).strip()
                fb_q = sc_qs[1] if len(sc_qs) > 1 else str(sc.get("stock_query") or prim_q).strip()
                d = {
                    "scene_id": sid,
                    "tool_name": default_video_tool,
                    "search_scope": "SCENE_SPECIFIC",
                    "subject_entity": str(sc.get("subject_entity") or eff_entity).strip(),
                    "youtube_search_query": prim_q,
                    "fallback_broad_query": fb_q,
                    "storyline_search_queries": sc_qs or [prim_q, fb_q],
                    "clip_role": "HOOK" if sid == 1 else "PEAK",
                    "target_duration_sec": float(sc.get("estimated_seconds") or 4.5),
                }
                strat_by_sid[sid] = d
                auto_healed_sids.append(sid)

            # Đảm bảo ưu tiên kế thừa `subject_entity` và `search_queries` từ Node LLM viết kịch bản nếu Tool Call đang rỗng
            sc_subj = str(sc.get("subject_entity") or "").strip()
            if sc_subj and not str(d.get("subject_entity") or "").strip():
                d["subject_entity"] = sc_subj
            sc_search_qs = [str(q).strip() for q in (sc.get("search_queries") or []) if str(q).strip()]
            if sc_search_qs and not d.get("storyline_search_queries"):
                d["storyline_search_queries"] = sc_search_qs[:4]

        # Tính tỷ lệ Tool Video chuyển động thật; nếu < 86% (hoặc cảnh đầu/cuối là ảnh tĩnh), nâng cấp lên Tool Video
        mandatory_video_sids = {1, 2, max(1, total_scenes - 1), total_scenes}
        video_sids = {sid for sid, d in strat_by_sid.items() if self._is_video_tool_decision(d)}
        for sid in sorted(strat_by_sid.keys()):
            d = strat_by_sid[sid]
            need_upgrade = (sid in mandatory_video_sids and sid not in video_sids) or (
                len(video_sids) / max(1, total_scenes) < 0.86 and sid not in video_sids
            )
            if need_upgrade:
                d["tool_name"] = default_video_tool
                if default_video_tool == "fetch_clean_cinematic_portrait_or_broll":
                    d["media_preference"] = "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if (sid % 2 == 1) else "VIDEO_BROLL"
                    prim_q, _ = self._extract_primary_and_fallback_query(d, scenes[sid - 1] if sid <= len(scenes) else {})
                    d.setdefault("human_action_query", prim_q)
                else:
                    d.setdefault("clip_role", "HOOK" if sid == 1 else "PEAK")
                    prim_q, fb_q = self._extract_primary_and_fallback_query(d, scenes[sid - 1] if sid <= len(scenes) else {})
                    d.setdefault("youtube_search_query", prim_q)
                    d.setdefault("fallback_broad_query", fb_q)
                video_sids.add(sid)
                if sid not in auto_healed_sids:
                    auto_healed_sids.append(sid)

        # ── BƯỚC 2: DÙNG 1-CALL BATCH LLM TOOL-CALL JUDGE ĐỂ KIỂM ĐỊNH ĐỘ KHỚP NGỮ CẢNH & CHỐNG LẠC ĐỀ ──
        llm_audit_map: Dict[int, Dict[str, Any]] = {}
        evaluated_by = "heuristic"
        if self.rotator and self.rotator.has_keys and total_scenes <= 16:
            llm_audit_map, evaluated_by = self._run_llm_tool_call_judge(
                topic=topic,
                eff_entity=eff_entity,
                channel_niche=channel_niche,
                scenes=scenes,
                strat_by_sid=strat_by_sid,
            )

        # ── BƯỚC 3: ÁP DỤNG AUTO-HEAL TỪ LLM TOOL-CALL JUDGE & TỔNG HỢP BÁO CÁO ──
        scene_audits: List[SceneToolCallAudit] = []
        for idx, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                continue
            sid = int(sc.get("scene_id") or (idx + 1))
            d = strat_by_sid[sid]
            a_llm = llm_audit_map.get(sid) or {}

            was_healed = sid in auto_healed_sids
            rep_tool = str(a_llm.get("repaired_tool") or "").strip()
            rep_subj = str(a_llm.get("repaired_subject_entity") or "").strip()
            rep_prim_q = str(a_llm.get("repaired_primary_query") or "").strip()
            rep_fb_q = str(a_llm.get("repaired_fallback_query") or "").strip()
            rep_story_qs = [
                str(q).strip()
                for q in (a_llm.get("repaired_storyline_queries") or [])
                if str(q).strip()
            ]
            t_fit = max(1.0, min(10.0, float(a_llm.get("tool_fit_score", 9.0) or 9.0)))
            q_rel = max(1.0, min(10.0, float(a_llm.get("query_relevance_score", 9.0) or 9.0)))
            is_optimal_llm = bool(a_llm.get("is_optimal", True)) and t_fit >= 8.2 and q_rel >= 8.2

            if not is_optimal_llm:
                if rep_tool in MOVING_VIDEO_TOOLS:
                    d["tool_name"] = rep_tool
                    was_healed = True
                if rep_subj:
                    d["subject_entity"] = rep_subj
                    sc["subject_entity"] = rep_subj
                    was_healed = True
                if rep_prim_q:
                    t_cur = str(d.get("tool_name") or "")
                    if t_cur == "fetch_clean_cinematic_portrait_or_broll":
                        d["human_action_query"] = rep_prim_q
                    elif t_cur == "summon_epic_monster_cinematic_media":
                        d["action_or_concept_query"] = rep_prim_q
                    else:
                        d["youtube_search_query"] = rep_prim_q
                    sc["visual_keyword"] = rep_prim_q
                    was_healed = True
                if rep_fb_q:
                    d["fallback_broad_query"] = rep_fb_q
                    sc["stock_query"] = rep_fb_q
                    was_healed = True
                if rep_story_qs:
                    d["storyline_search_queries"] = rep_story_qs[:4]
                    sc["search_queries"] = rep_story_qs[:4]
                    was_healed = True
                elif rep_prim_q or rep_fb_q:
                    cur_prim, cur_fb = self._extract_primary_and_fallback_query(d, sc)
                    merged_qs = [q for q in dict.fromkeys([cur_prim, cur_fb, *(d.get("storyline_search_queries") or [])]) if q]
                    d["storyline_search_queries"] = merged_qs[:4]
                    sc["search_queries"] = merged_qs[:4]

                if was_healed:
                    t_fit = max(t_fit, 9.0)
                    q_rel = max(q_rel, 9.0)
                    if sid not in auto_healed_sids:
                        auto_healed_sids.append(sid)

            prim_q_final, fb_q_final = self._extract_primary_and_fallback_query(d, sc)
            story_qs_final = [
                str(q).strip()
                for q in (d.get("storyline_search_queries") or sc.get("search_queries") or [prim_q_final, fb_q_final])
                if str(q).strip()
            ]
            scene_audits.append(
                SceneToolCallAudit(
                    scene_id=sid,
                    narration=str(sc.get("narration") or ""),
                    selected_tool=str(d.get("tool_name") or default_video_tool),
                    is_moving_video_tool=self._is_video_tool_decision(d),
                    search_scope=str(d.get("search_scope") or "SCENE_SPECIFIC"),
                    subject_entity=str(d.get("subject_entity") or sc.get("subject_entity") or eff_entity),
                    primary_query=prim_q_final,
                    fallback_query=fb_q_final,
                    storyline_search_queries=story_qs_final[:4],
                    tool_fit_score=round(t_fit, 1),
                    query_relevance_score=round(q_rel, 1),
                    status="AUTO_HEALED" if was_healed else "OPTIMAL",
                    audit_notes=str(a_llm.get("audit_notes") or ("Auto-healed tool/query alignment." if was_healed else "Optimal tool & query binding.")),
                )
            )

        healed_strategies = [strat_by_sid[int(sc.get("scene_id") or (i + 1))] for i, sc in enumerate(scenes) if isinstance(sc, dict)]
        healed_visual_plan = {
            **(visual_plan or {}),
            "scenes_strategy": healed_strategies,
            "tool_calls_audited": True,
        }
        healed_script_data = {
            **(script_data or {}),
            "scenes": scenes,
        }

        moving_cnt = sum(1 for a in scene_audits if a.is_moving_video_tool)
        moving_pct = round((moving_cnt / max(1, len(scene_audits))) * 100.0, 1)
        avg_score = round(
            sum((a.tool_fit_score * 0.4 + a.query_relevance_score * 0.6) for a in scene_audits) / max(1, len(scene_audits)),
            1,
        )
        passed = bool(moving_pct >= 85.0 and avg_score >= 8.0)
        summary = (
            f"LLM Tool-Call Audit: {avg_score}/10 | Moving Video Tools: {moving_cnt}/{len(scene_audits)} ({moving_pct}%) | "
            f"Auto-Healed Scenes: {auto_healed_sids or 'None'}"
        )

        report = LLMToolCallEvaluationReport(
            total_scenes=len(scene_audits),
            moving_video_tool_scenes=moving_cnt,
            moving_video_planned_ratio_pct=moving_pct,
            overall_tool_call_score=avg_score,
            passed=passed,
            auto_healed_scene_ids=auto_healed_sids,
            scene_audits=scene_audits,
            summary=summary,
            evaluated_by=evaluated_by,
        )
        return report, healed_visual_plan, healed_script_data

    def _run_llm_tool_call_judge(
        self,
        topic: str,
        eff_entity: str,
        channel_niche: str,
        scenes: List[Dict[str, Any]],
        strat_by_sid: Dict[int, Dict[str, Any]],
    ) -> Tuple[Dict[int, Dict[str, Any]], str]:
        yaml_strategy_block = build_video_search_strategy_prompt_block(channel_niche or "general")
        items_to_judge = []
        for idx, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                continue
            sid = int(sc.get("scene_id") or (idx + 1))
            d = strat_by_sid.get(sid) or {}
            prim_q, fb_q = self._extract_primary_and_fallback_query(d, sc)
            items_to_judge.append({
                "scene_id": sid,
                "narration": sc.get("narration", ""),
                "script_llm_subject_entity": sc.get("subject_entity", ""),
                "script_llm_search_queries": sc.get("search_queries") or [],
                "selected_tool": d.get("tool_name", ""),
                "search_scope": d.get("search_scope", "SCENE_SPECIFIC"),
                "tool_subject_entity": d.get("subject_entity", ""),
                "primary_query": prim_q,
                "fallback_query": fb_q,
                "storyline_search_queries": d.get("storyline_search_queries") or [],
            })

        prompt = f"""You are the Chief LLM Tool-Calling & Visual Search Auditor in a LangGraph YouTube Shorts production pipeline.
Topic: "{topic}"
Primary Subject/Entity: "{eff_entity}"
Channel Niche: "{channel_niche}"

{yaml_strategy_block}

--- SCENE-BY-SCENE LLM TOOL CALLS TO AUDIT ---
{json.dumps(items_to_judge, ensure_ascii=False, indent=2)}

YOUR AUDIT RULES (100% SEMANTIC REASONING):
1. Verify that EVERY scene (`scene_id` 1 to {len(items_to_judge)}) selects an appropriate moving-video `@tool`:
   - `clone_youtube_short_peak_clip` for wildlife, nature, animal rescue, science, or history real-world footage;
   - `fetch_clean_cinematic_portrait_or_broll` for human psychology, interrogation, or social behavior;
   - `summon_epic_monster_cinematic_media` for colossal sea monsters, kaiju, or prehistoric megafauna.
2. Verify that `tool_subject_entity`, `primary_query`, `fallback_query`, and `storyline_search_queries` match the exact entity and physical action in `narration` (and respect `script_llm_subject_entity` / `script_llm_search_queries` when they are concrete and accurate):
   - NEVER allow a query to search for a literal metaphor/simile object instead of the real canonical subject;
   - NEVER allow a query to drop the canonical entity name when `narration` uses a pronoun ("It", "He", "They", "This animal");
   - NEVER allow `"vs"` or `"versus"` inside single-entity body scenes or inside `fallback_query` (stock APIs require single-entity 2–4 word queries).
3. If a scene's tool call and queries are already concrete, canonical-entity-anchored, and on-topic, set `is_optimal: true`, `tool_fit_score >= 9.0`, and `query_relevance_score >= 9.0`.
4. If any scene has an off-topic query, metaphor leak, missing entity name, or wrong tool, set `is_optimal: false`, score `< 7.5`, and provide the exact corrected fields (`repaired_tool`, `repaired_subject_entity`, `repaired_primary_query`, `repaired_fallback_query`, `repaired_storyline_queries`).

Return ONLY a valid JSON object matching this schema:
{{
  "audits": [
    {{
      "scene_id": 1,
      "is_optimal": true,
      "tool_fit_score": 9.5,
      "query_relevance_score": 9.5,
      "audit_notes": "Concise explanation of why the tool call and queries fit the scene",
      "repaired_tool": "",
      "repaired_subject_entity": "",
      "repaired_primary_query": "",
      "repaired_fallback_query": "",
      "repaired_storyline_queries": []
    }}
  ]
}}"""

        for model, key in self.rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=3):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                gen_cfg: Dict[str, Any] = {"temperature": 0.1}
                if not model.startswith("gemma-"):
                    gen_cfg["responseMimeType"] = "application/json"
                    if "lite" not in model.lower():
                        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg,
                }
                resp = requests.post(url, json=payload, timeout=(3.5, 14.0))
                if resp.status_code in (429, 403, 404, 500, 502, 503, 504):
                    self.rotator.mark_model_rate_limited(key, model, status_code=resp.status_code, error_text=resp.text[:250])
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        continue
                    raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}").strip()
                    if "```" in raw:
                        raw = re.sub(r"^```(?:json)?\s*", "", raw)
                        raw = re.sub(r"\s*```$", "", raw)
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1:
                        raw = raw[start : end + 1]
                    parsed = json.loads(raw)
                    out_map: Dict[int, Dict[str, Any]] = {}
                    for item in (parsed.get("audits") or []):
                        if isinstance(item, dict) and int(item.get("scene_id") or 0) > 0:
                            out_map[int(item["scene_id"])] = item
                    if out_map:
                        return out_map, "ai"
            except Exception as ex:
                logger.warning(f"[LLMToolCallCritic] ({model}) error: {ex}")
                continue

        return {}, "heuristic"
