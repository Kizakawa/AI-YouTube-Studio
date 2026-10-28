"""
app.agents.content.longform_10min_generator — Bộ máy Sinh Kịch Bản Phim Tài Liệu Dài 10 Phút 16:9 (10 Chapters / 40 Scenes / ~1,450–1,600 Words).

Kiến trúc 2 Tầng Song Song (2-Stage Showrunner Outline + 5-Way Parallel Chapter Batch Expansion):
1. Stage 1 (Master 10-Chapter Showrunner Blueprint - 1 LLM Call):
   - Thiết kế cấu trúc 10 Chương phim tài liệu độc lập, không trùng lặp ý hay ví dụ giữa các chương.
   - Mỗi Chương gắn với 1 mã truy vấn hình ảnh 16:9 riêng biệt (`chapter_master_query`) để `LongFormChapterPool` tải 10 video nguồn khác nhau.
2. Stage 2 (5-Batch Parallel Scene Expansion - 5 LLM Calls chạy đồng thời qua ThreadPoolExecutor):
   - Mỗi Batch viết sâu 2 Chương (8 phân cảnh / Batch -> tổng 40 phân cảnh).
   - Mỗi phân cảnh thân bài gồm 2 câu văn phim tài liệu sắc bén (34–42 từ/cảnh, ~14–16 giây/cảnh) -> Tổng ~1,450–1,600 từ (~9.5 – 10.5 phút).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)


DEFAULT_10_CHAPTER_BLUEPRINT = [
    {
        "chapter_id": 1,
        "chapter_title": "CHAPTER 1: THE 4-SECOND ANOMALY",
        "chapter_premise": "Cold-open confrontation exposing the hidden micro-expression and the first 4-second silence anomaly.",
        "chapter_master_query": "tense boardroom meeting eye contact",
    },
    {
        "chapter_id": 2,
        "chapter_title": "CHAPTER 2: THE AMYGDALA HIJACK",
        "chapter_premise": "How covert gaslighting and sudden guilt-tripping bypass rational prefrontal cortex defenses in 0.2 seconds.",
        "chapter_master_query": "neuroscience brain scan laboratory monitor",
    },
    {
        "chapter_id": 3,
        "chapter_title": "CHAPTER 3: THE BAIT-AND-SWITCH TRAP",
        "chapter_premise": "Forensic breakdown of how manipulators weaponize polite smiles and fake concern to force defensive over-explaining.",
        "chapter_master_query": "fbi detective interrogation suspect table",
    },
    {
        "chapter_id": 4,
        "chapter_title": "CHAPTER 4: CASE STUDY — THE BOARDROOM GAMBIT",
        "chapter_premise": "Real-world high-stakes negotiation case study where a 4-second pause dismantled an aggressive power play.",
        "chapter_master_query": "corporate executive negotiation glass office",
    },
    {
        "chapter_id": 5,
        "chapter_title": "CHAPTER 5: THE DOPAMINE APPROVAL LOOP",
        "chapter_premise": "Why chronic people-pleasers fall into intermittent reinforcement and cortisol exhaustion when seeking validation.",
        "chapter_master_query": "person walking alone night city rain",
    },
    {
        "chapter_id": 6,
        "chapter_title": "CHAPTER 6: MICRO-EXPRESSIONS OF DECEPTION",
        "chapter_premise": "The three involuntary physical tells—frozen feet, rapid blinking, and vocal pitch shifts—that expose hidden hostility.",
        "chapter_master_query": "close up human eye pupil dark lighting",
    },
    {
        "chapter_id": 7,
        "chapter_title": "CHAPTER 7: THE MIRROR QUESTION PROTOCOL",
        "chapter_premise": "Surgical verbal counter-move: how asking a calm, slow clarifying question forces the manipulator to audit their own words.",
        "chapter_master_query": "chess grandmaster moving king dark board",
    },
    {
        "chapter_id": 8,
        "chapter_title": "CHAPTER 8: THE DOUBLE-BIND ESCAPE",
        "chapter_premise": "Dismantling false binary choices and guilt traps without raising your voice or surrendering emotional control.",
        "chapter_master_query": "businessman looking out window night skyline",
    },
    {
        "chapter_id": 9,
        "chapter_title": "CHAPTER 9: ARCHITECTING UNSHAKEABLE BOUNDARIES",
        "chapter_premise": "Replacing reactive defensiveness with sovereign body language, low vocal cadence, and zero-justification detachment.",
        "chapter_master_query": "confident leader walking modern corridor",
    },
    {
        "chapter_id": 10,
        "chapter_title": "CHAPTER 10: THE SOVEREIGN MINDSET",
        "chapter_premise": "Final synthesis of the 10-minute behavioral protocol and seamless conceptual loop back to the opening 4-second silence.",
        "chapter_master_query": "cinematic silhouette man sunrise horizon",
    },
]


def _extract_json_dict(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return json.loads(text[s : e + 1])
    return {}


def generate_10min_longform_documentary(
    agent: Any,
    topic: str,
    channel_niche: str,
    language: str,
    target_lang_display: str,
    lang_instruction: str,
    director_block: str,
    channel_memory_block: str,
    niche_instructions: str,
) -> Optional[ScriptContract]:
    """
    Sinh kịch bản Phim Tài Liệu Dài ~10 Phút (10 Chương / 40 Phân cảnh / ~1,450–1,600 từ).
    """
    outline_prompt = f"""You are an Executive Showrunner for a 10-Minute Widescreen 16:9 YouTube Documentary on '{channel_niche}'.
Topic: "{topic}"
Target Language: {language} ({target_lang_display})
{lang_instruction}
{director_block}
{niche_instructions[:2600] if niche_instructions else ""}
{channel_memory_block[:1800]}

DESIGN A 10-CHAPTER MASTER DOCUMENTARY OUTLINE (TARGET: 10 MINUTES / 40 SCENES / ~1,500 WORDS):
1. `title`: High-CTR Search-Optimized Documentary Title in {target_lang_display} (32-58 chars, explicitly naming key entities/mechanisms + concrete metric or Competitor Archetype like "Animal Vs. The Gauntlet", "Every [Phenomenon] Record Explained", or "What If You...", NO '#Shorts' tag).
2. `hook`: Scene 1 Cold-Open Psychological-Visual Hook in {target_lang_display} (10-16 words, declarative statement/observation, NO '?' questions, NEVER repeats title verbatim).
3. `thumbnail_text`: MAXIMUM 2 TO 3 ULTRA-PUNCHY ALL-CAPS WORDS (e.g. "TOO EASY?", "TURN INVISIBLE.", "512 MPH!", "4-SECOND SILENCE") following the YAML Competitor Blueprint.
4. `chapters`: Exactly 10 distinct Documentary Chapters / Rounds (`chapter_id` 1 to 10). Follow the active niche's YAML `competitor_blueprint` (e.g. Round-by-Round Gauntlet escalation for wildlife, Every Record/Depth Tier Explained for deep sea/earth mysteries, or Hour 1 -> Year 10 escalation for psychology/alternate history) with ZERO repetition across chapters!
5. `chapter_master_query`: For each chapter, provide a distinct 3-5 word English search query for 16:9 documentary video footage matching that specific chapter's subject/challenger/setting. Every chapter MUST have a completely different visual environment!

Return ONLY valid JSON:
{{
  "title": "High-CTR 16:9 Documentary Title in {target_lang_display}",
  "title_variants": ["Variant 1", "Variant 2", "Variant 3"],
  "primary_entity": "Core subject/entity in 1-2 English words",
  "hook": "10-16 word Cold-Open Hook in {target_lang_display}",
  "thumbnail_text": "2-3 ALL-CAPS WORDS",
  "description": " gripping documentary description with timestamps and hashtags",
  "pinned_comment": "14-22 word Counterfactual 50/50 Debate Question ending with (1) ... or (2) ... 👇",
  "tags": ["documentary", "psychology", "viral"],
  "hook_search_queries": ["query 1", "query 2", "query 3"],
  "chapters": [
    {{
      "chapter_id": 1,
      "chapter_title": "CHAPTER 1: ...",
      "chapter_premise": "Specific mechanism, case study, and concrete metric covered in Chapter 1",
      "chapter_master_query": "3-5 word English 16:9 video search query"
    }}
  ]
}}"""

    outline_data: Dict[str, Any] = {}
    try:
        raw_outline = agent._call_api(outline_prompt, json_mode=True, timeout=32, max_total_seconds=50)
        outline_data = _extract_json_dict(raw_outline)
    except Exception as e_out:
        logger.warning(f"[LongForm10Min] Stage-1 Outline warning ({e_out}), using structured fallback blueprint.")

    chapters_list = outline_data.get("chapters") or []
    if not isinstance(chapters_list, list) or len(chapters_list) < 10:
        merged_ch = []
        for idx_c in range(10):
            if idx_c < len(chapters_list) and isinstance(chapters_list[idx_c], dict):
                item_c = dict(DEFAULT_10_CHAPTER_BLUEPRINT[idx_c])
                item_c.update({k: v for k, v in chapters_list[idx_c].items() if v})
                item_c["chapter_id"] = idx_c + 1
                merged_ch.append(item_c)
            else:
                merged_ch.append(dict(DEFAULT_10_CHAPTER_BLUEPRINT[idx_c]))
        chapters_list = merged_ch
    else:
        chapters_list = chapters_list[:10]

    doc_title = str(outline_data.get("title") or f"{topic}: The Complete 10-Minute Forensic Breakdown 🧠").strip()
    doc_entity = str(outline_data.get("primary_entity") or topic).strip()
    doc_hook = str(
        outline_data.get("hook")
        or "Watch their eyes during the four seconds of silence right after they cross your boundary."
    ).strip()

    chapter_summary_text = "\n".join(
        f"- Ch.{c.get('chapter_id')}: {c.get('chapter_title')} -> {c.get('chapter_premise')} [Visual: {c.get('chapter_master_query')}]"
        for c in chapters_list
    )

    def _expand_chapter_pair(batch_idx: int) -> List[Dict[str, Any]]:
        ch_a = chapters_list[batch_idx * 2]
        ch_b = chapters_list[batch_idx * 2 + 1]
        start_sid = batch_idx * 8 + 1
        end_sid = start_sid + 7

        batch_prompt = f"""You are writing Scenes {start_sid} to {end_sid} (Chapters {ch_a['chapter_id']} and {ch_b['chapter_id']}) of a 10-Minute Widescreen 16:9 YouTube Documentary in {target_lang_display}.
Documentary Title: "{doc_title}"
Topic: "{topic}"
{lang_instruction}

FULL 10-CHAPTER DOCUMENTARY ROADMAP (FOR CONTEXT — DO NOT REPEAT OTHER CHAPTERS):
{chapter_summary_text}

YOUR ASSIGNED CHAPTERS TO WRITE RIGHT NOW (8 SCENES TOTAL: SCENES {start_sid} TO {end_sid}):
1. Chapter {ch_a['chapter_id']} ({ch_a['chapter_title']}) — Scenes {start_sid}, {start_sid+1}, {start_sid+2}, {start_sid+3}:
   - Premise: {ch_a['chapter_premise']}
   - Chapter 16:9 Visual Motif: "{ch_a['chapter_master_query']}"
2. Chapter {ch_b['chapter_id']} ({ch_b['chapter_title']}) — Scenes {start_sid+4}, {start_sid+5}, {start_sid+6}, {start_sid+7}:
   - Premise: {ch_b['chapter_premise']}
   - Chapter 16:9 Visual Motif: "{ch_b['chapter_master_query']}"

MANDATORY PACING & WORD COUNT FOR A TRUE 10-MINUTE DOCUMENTARY (~14–16 SECONDS PER SCENE):
- {'For Scene 1 (scene_id=1): Start with the Cold-Open Hook ("' + doc_hook + '") followed by 1 gripping follow-up sentence (total 24–32 words for Scene 1).' if start_sid == 1 else f'For every scene (Scenes {start_sid} to {end_sid}): Write 2 to 3 complete, rich, gripping documentary sentences totaling 35 to 44 WORDS PER SCENE (~14–16 seconds of spoken narration per scene).'}
- For Scenes {start_sid+1 if start_sid == 1 else start_sid} to {end_sid}: Every single scene MUST contain 35 to 44 words of deep, vivid storytelling (real-world dialogue examples, neurological/physical metrics, forensic observations, or surgical counter-moves). NEVER write short 12-word Shorts sentences!
- ZERO SPOKEN OUTRO CTA: Never say "Subscribe", "Like", or "Leave a comment" in the narration.
- Self-Contained Visual Queries:
  * For Scene {start_sid} (start of Chapter {ch_a['chapter_id']}), anchor `visual_keyword` and `stock_query` to "{ch_a['chapter_master_query']}".
  * For Scene {start_sid+4} (start of Chapter {ch_b['chapter_id']}), anchor `visual_keyword` and `stock_query` to "{ch_b['chapter_master_query']}".
  * For all other scenes in each chapter, write concrete 3–5 word English physical action queries matching that chapter's setting.

Return ONLY valid JSON with key "scenes" containing exactly 8 scene objects (`scene_id` {start_sid} through {end_sid}):
{{
  "scenes": [
    {{
      "scene_id": {start_sid},
      "visual_reasoning": "Chapter {ch_a['chapter_id']} visual intent",
      "narration": "2 to 3 complete documentary sentences in {target_lang_display} totaling 35 to 44 words...",
      "visual_keyword": "{ch_a['chapter_master_query']}",
      "visual_prompt": "Cinematic 16:9 widescreen shot of {ch_a['chapter_master_query']}",
      "stock_query": "{' '.join(str(ch_a['chapter_master_query']).split()[:3])}",
      "media_type": "video",
      "camera_motion": "zoom_in_fast",
      "sfx_cue": "impact_boom",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": null,
      "estimated_seconds": 14.5
    }}
  ]
}}"""
        for attempt_b in range(2):
            try:
                raw_b = agent._call_api(batch_prompt, json_mode=True, timeout=40, max_total_seconds=65)
                b_dict = _extract_json_dict(raw_b)
                b_scenes = b_dict.get("scenes") or []
                if isinstance(b_scenes, list) and len(b_scenes) >= 6:
                    # Chuẩn hóa đúng 8 cảnh cho batch
                    normalized_b: List[Dict[str, Any]] = []
                    for idx_local in range(8):
                        target_id = start_sid + idx_local
                        ch_cur = ch_a if idx_local < 4 else ch_b
                        if idx_local < len(b_scenes) and isinstance(b_scenes[idx_local], dict):
                            sc_item = dict(b_scenes[idx_local])
                        else:
                            sc_item = {}
                        sc_item["scene_id"] = target_id
                        ch_title_cur = str(ch_cur.get("chapter_title") or f"CHAPTER {ch_cur.get('chapter_id')}").strip()
                        cur_vr = str(sc_item.get("visual_reasoning") or "").strip()
                        if ch_title_cur.lower() not in cur_vr.lower():
                            sc_item["visual_reasoning"] = f"{ch_title_cur} — {cur_vr}" if cur_vr else ch_title_cur
                        narr_txt = str(sc_item.get("narration") or "").strip()
                        if target_id == 1:
                            # Scene 1 Cold-Open Hook: giữ 14–26 từ sắc bén, không câu hỏi '?'
                            narr_txt = narr_txt.replace("?", ".")
                            s1_words = narr_txt.split()
                            if len(s1_words) > 28:
                                narr_txt = " ".join(s1_words[:26]).rstrip(",;:") + "."
                            elif len(s1_words) < 12:
                                narr_txt = doc_hook.replace("?", ".")
                        elif len(narr_txt.split()) < 35:
                            extra_premise = str(ch_cur.get("chapter_premise") or "").strip().rstrip(".")
                            narr_txt = (
                                f"{narr_txt} In forensic behavioral analysis, {extra_premise[:110].lower()}, "
                                f"exposing the underlying psychological leverage before the other person can recalibrate their script."
                            ).strip()
                        sc_item["narration"] = narr_txt
                        if idx_local in (0, 4) or not sc_item.get("visual_keyword"):
                            sc_item["visual_keyword"] = str(ch_cur.get("chapter_master_query") or "tense business meeting")
                        if idx_local in (0, 4) or not sc_item.get("stock_query"):
                            sc_item["stock_query"] = " ".join(str(ch_cur.get("chapter_master_query") or "business meeting").split()[:3])
                        if not sc_item.get("visual_prompt"):
                            sc_item["visual_prompt"] = f"Cinematic 16:9 widescreen documentary shot of {sc_item['visual_keyword']}"
                        sc_item["media_type"] = "video"
                        if idx_local in (0, 4):
                            ch_lbl = str(ch_cur.get("chapter_title") or f"CHAPTER {ch_cur.get('chapter_id', 1)}").strip()
                            sc_item["overlay_badge"] = f"[ // {ch_lbl.upper()} // ]"
                        else:
                            sc_item["overlay_badge"] = None
                        sc_item["estimated_seconds"] = round(max(11.0, len(narr_txt.split()) / 2.45), 1)
                        normalized_b.append(sc_item)
                    return normalized_b
            except Exception as e_b:
                logger.warning(f"[LongForm10Min] Batch {batch_idx+1}/5 attempt {attempt_b+1} warning: {e_b}")

        # Fallback an toàn nếu 1 batch gặp lỗi mạng
        fallback_b: List[Dict[str, Any]] = []
        for idx_local in range(8):
            target_id = start_sid + idx_local
            ch_cur = ch_a if idx_local < 4 else ch_b
            premise_clean = str(ch_cur.get("chapter_premise") or "behavioral power dynamics in high-stakes negotiation").strip().rstrip(".")
            m_query = str(ch_cur.get("chapter_master_query") or "tense executive boardroom meeting").strip()
            narr_fb = (
                f"When we examine {premise_clean.lower()}, researchers discovered that ninety percent of people react within two seconds out of pure conditioned habit. "
                f"By slowing your cadence and holding steady eye contact for four full seconds, you strip away their psychological leverage completely."
            )
            ch_title_fb = str(ch_cur.get("chapter_title") or f"CHAPTER {ch_cur.get('chapter_id', 1)}").strip().upper()
            ch_badge = f"[ // {ch_title_fb} // ]" if idx_local in (0, 4) else None
            fallback_b.append({
                "scene_id": target_id,
                "visual_reasoning": f"{ch_cur.get('chapter_title')} documentary beat",
                "narration": narr_fb,
                "visual_keyword": m_query,
                "visual_prompt": f"Cinematic 16:9 widescreen documentary shot of {m_query}",
                "stock_query": " ".join(m_query.split()[:3]),
                "media_type": "video",
                "camera_motion": "zoom_in" if idx_local % 2 == 0 else "pan_right",
                "sfx_cue": "none",
                "highlight_words": ["FOUR", "SECONDS"],
                "overlay_badge": ch_badge,
                "estimated_seconds": 14.5,
            })
        return fallback_b

    logger.info("🎬 [LongForm10Min] Đang chạy song song 5 Batch LLM viết 10 Chương (40 phân cảnh / ~1,500 từ / ~10 phút)...")
    with ThreadPoolExecutor(max_workers=5) as pool:
        batch_results = list(pool.map(_expand_chapter_pair, range(5)))

    all_scenes: List[Dict[str, Any]] = []
    for b_list in batch_results:
        all_scenes.extend(b_list)

    for idx_s, sc in enumerate(all_scenes, start=1):
        sc["scene_id"] = idx_s
        if (idx_s - 1) % 4 != 0:
            sc["overlay_badge"] = None

    full_narr = " ".join(str(s.get("narration") or "").strip() for s in all_scenes if s.get("narration"))
    total_w = len(full_narr.split())
    logger.info(
        f"✅ [LongForm10Min] Hoàn tất kịch bản Phim Tài Liệu 10 Chương: "
        f"{len(all_scenes)} phân cảnh | Tổng số từ: {total_w} từ (~{total_w / 2.45:.0f}s / ~{total_w / 148.0:.1f} phút)!"
    )

    assembled_json = {
        "video_format": "long",
        "title": doc_title,
        "title_variants": outline_data.get("title_variants") or [doc_title],
        "primary_entity": doc_entity,
        "hook": str(all_scenes[0].get("narration") or doc_hook),
        "hook_variants": [doc_hook],
        "hook_visual_mode": "video",
        "is_abstract_keyword": False,
        "climax_scene_id": 20,
        "hook_search_queries": outline_data.get("hook_search_queries") or ["tense boardroom meeting", "fbi interrogation eye contact"],
        "full_narration": full_narr,
        "thumbnail_text": str(outline_data.get("thumbnail_text") or "4-SECOND SILENCE 🧠"),
        "scenes": all_scenes,
        "tags": outline_data.get("tags") or ["documentary", "psychology", "viral"],
        "description": str(outline_data.get("description") or f"{doc_title} — Full 10-Minute Widescreen Documentary."),
        "pinned_comment": str(
            outline_data.get("pinned_comment")
            or "In a high-stakes confrontation, which move works better for you: (1) The 4-Second Silence or (2) The Mirror Question? 👇"
        ),
        "viral_formula": "10-Chapter Widescreen Documentary | Multi-Source YouTube 16:9 Chapter Pool | Internal Jump-Cuts",
    }

    raw_assembled_str = json.dumps(assembled_json, ensure_ascii=False)
    contract = agent._parse_json_script(
        raw_assembled_str,
        channel_niche=channel_niche,
        topic=topic,
        video_format="long",
    )
    return contract
