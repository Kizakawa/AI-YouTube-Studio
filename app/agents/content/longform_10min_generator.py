"""
app.agents.content.longform_10min_generator — Bộ máy Sinh Kịch Bản Phim Tài Liệu Dài 10 Phút 16:9
(10 Chapters / 40 Scenes / ~1,650–1,900 Words / Cam kết vượt ngưỡng >= 9m15s Mid-Roll Ads).

Kiến trúc 2 Tầng Song Song + Hậu kiểm Độ dài Tự động (100% YAML SSOT qua `niche_manager.py`):
1. Stage 1 (Master 10-Chapter Showrunner Blueprint - 1 LLM Call):
   - Thiết kế cấu trúc 10 Chương phim tài liệu leo thang (Delayed Main-Event Payoff), không trùng lặp ý giữa các chương.
   - Mỗi Chương chỉ định rõ `chapter_subject_entity` (Tiếng Anh chuẩn) và `chapter_master_query` (truy vấn 16:9 Tiếng Anh)
     để `LongFormChapterPool` tải 10 video nguồn khác nhau mà không bị nhiễm thực thể Chương 1 sang Chương 2–10.
2. Stage 2 (5-Batch Parallel Scene Expansion - 5 LLM Calls chạy đồng thời qua ThreadPoolExecutor):
   - Mỗi Batch viết sâu 2 Chương (4 cảnh / Chương = 8 phân cảnh / Batch -> tổng 40 phân cảnh).
   - Mỗi phân cảnh gồm 3–4 câu văn kể chuyện tự nhiên (43–50 từ/cảnh EN, 40–47 từ/cảnh PT-BR -> ~14–16 giây/cảnh)
     -> Tổng ~1,650–1,900 từ (~9m30s – 10m40s).
3. Stage 3 (Mid-Roll Word-Floor Expansion Guard):
   - Nếu tổng số từ lời thoại thấp hơn ngưỡng an toàn (`1,580` từ EN / `1,480` từ PT-BR), tự động gọi 1 lượt LLM Batch
     bổ sung chiều sâu diễn biến cho các cảnh ngắn nhất để đảm bảo 100% vượt mốc `9m15s` (`555,000ms`) trước khi sang TTS.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.schemas.contracts import ScriptContract
from app.services.niche_manager import (
    build_million_view_showrunner_prose_dna,
    build_video_search_strategy_prompt_block,
    get_niche,
)

logger = logging.getLogger(__name__)

SCENES_PER_CHAPTER_LONGFORM = 4
TOTAL_CHAPTERS_LONGFORM = 10
TOTAL_SCENES_LONGFORM = TOTAL_CHAPTERS_LONGFORM * SCENES_PER_CHAPTER_LONGFORM


# Khung 10 chương DỰ PHÒNG trung lập theo ngôn ngữ & chủ đề (chỉ dùng khi LLM outline trả thiếu chương).
DEFAULT_10_CHAPTER_ROLES_EN = [
    ("THE COLD-OPEN TEASE & SUSPENDED CLIMAX", "Plunge into the highest-stakes moment of {topic}, suspend the outcome as an unresolved Open Loop, and launch the escalation ladder."),
    ("TIER 1 — THE OPENING THRESHOLD", "The entry-level encounter or first real-world layer of {topic} that sets the baseline scale before stakes multiply."),
    ("TIER 2 — THE HIDDEN SPEED TRAP", "An unexpected reflex or rapid mechanism in {topic} that outpaces ordinary defenses and escalates past Tier 1."),
    ("TIER 3 — THE ARMOR & PRESSURE TEST", "A documented confrontation in {topic} where raw force meets specialized biological, historical, or behavioral defense."),
    ("TIER 4 — THE MID-POINT PARADOX", "The counter-intuitive weapon or unseen turning point in {topic} that flips size or power advantage in seconds."),
    ("TIER 5 — THE EXTREME ENVIRONMENT", "Entering the harshest zone of {topic} where normal strategies fail and only extreme adaptations survive."),
    ("TIER 6 — THE PENULTIMATE CHALLENGER", "The second-most dangerous rival or crisis in {topic} that pushes every limit right before the main event."),
    ("THE MAIN EVENT SHOWDOWN — ACT I", "Unleashing the headline confrontation or core mystery promised in the title of {topic}: the initial collision."),
    ("THE MAIN EVENT SHOWDOWN — ACT II", "The decisive turning point in {topic} where a hidden counter-mechanism seals the outcome."),
    ("THE FINAL VERDICT & RESOLUTION", "Closing synthesis that resolves the exact cliffhanger suspended in Chapter 1 of {topic}."),
]

DEFAULT_10_CHAPTER_ROLES_PT = [
    ("O CONFRONTO INICIAL E MISTÉRIO SUSPENSO", "Mergulhe no instante mais tenso de {topic}, congele o desfecho em aberto e inicie a escalada real."),
    ("NÍVEL 1 — O PRIMEIRO TESTE", "O encontro inicial ou primeira camada real de {topic} que define a escala do desafio."),
    ("NÍVEL 2 — A ARMADILHA DE VELOCIDADE", "Um mecanismo inesperado em {topic} que supera reflexos comuns e eleva o risco além do Nível 1."),
    ("NÍVEL 3 — O TESTE DE FORÇA E BLINDAGEM", "Um caso real documentado em {topic} onde força bruta encontra defesa especializada."),
    ("NÍVEL 4 — O PARADOXO DO MEIO", "A arma contraintuitiva ou virada invisível em {topic} que anula a vantagem de tamanho em segundos."),
    ("NÍVEL 5 — O AMBIENTE EXTREMO", "A zona mais letal de {topic} onde estratégias comuns falham por completo."),
    ("NÍVEL 6 — O PENÚLTIMO DESAFIANTE", "O segundo maior rival ou crise de {topic} logo antes do duelo principal."),
    ("O GRANDE DUELO PRINCIPAL — ATO I", "O confronto central prometido no título de {topic} finalmente começa com força total."),
    ("O GRANDE DUELO PRINCIPAL — ATO II", "A reviravolta decisiva em {topic} em que um mecanismo oculto define quem vence."),
    ("O VEREDITO FINAL E DESFECHO", "A revelação final que resolve o gancho aberto no Capítulo 1 de {topic}."),
]


def _chapter_word_for_lang(language: str) -> str:
    lang_low = str(language or "en").strip().lower()
    if lang_low.startswith("pt"):
        return "CAPÍTULO"
    if lang_low.startswith("vi"):
        return "CHƯƠNG"
    return "CHAPTER"


def _chapters_heading_for_lang(language: str) -> str:
    lang_low = str(language or "en").strip().lower()
    if lang_low.startswith("pt"):
        return "⏱️ Capítulos:"
    if lang_low.startswith("vi"):
        return "⏱️ Các chương chính:"
    return "⏱️ Chapters:"


def build_default_chapter_blueprint(topic: str, language: str = "en") -> List[Dict[str, Any]]:
    """Sinh khung 10 chương dự phòng trung lập theo ngôn ngữ kênh, neo vào chủ đề người dùng."""
    t = (topic or "this documentary").strip()
    ascii_words = re.findall(r"[A-Za-z0-9]+", t)
    base_query = " ".join(ascii_words[:4]) if ascii_words else "cinematic nature documentary"
    base_subj = " ".join(ascii_words[:2]) if ascii_words else "documentary subject"
    ch_prefix = _chapter_word_for_lang(language)
    roles = DEFAULT_10_CHAPTER_ROLES_PT if str(language or "").lower().startswith("pt") else DEFAULT_10_CHAPTER_ROLES_EN
    return [
        {
            "chapter_id": i + 1,
            "chapter_title": f"{ch_prefix} {i + 1}: {role}",
            "chapter_premise": premise.format(topic=t),
            "chapter_subject_entity": base_subj,
            "chapter_master_query": base_query,
        }
        for i, (role, premise) in enumerate(roles)
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
        candidate = text[s : e + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[s:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return {}


def _mine_real_world_research_dossier(topic: str, channel_niche: str, language: str = "en") -> str:
    """
    Bộ máy Thu thập Hồ sơ Dữ liệu Thật (Real-World Research Dossier) trước khi viết kịch bản:
    1. Quét trực tiếp Top Video Triệu View trên YouTube (`yt_dlp`) về chủ đề `topic`.
    2. Truy vấn Wikipedia API đúng ngôn ngữ kênh (`pt.wikipedia.org` / `vi.wikipedia.org` + fallback `en.wikipedia.org`)
       để lấy dữ kiện khoa học, lịch sử, sinh học xác thực 100%.
    """
    import requests as _req

    yt_lines: List[str] = []
    wiki_lines: List[str] = []
    lang_low = str(language or "en").strip().lower()

    def _fetch_yt_outliers() -> None:
        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "skip_download": True,
                "socket_timeout": 4,
                "ignoreerrors": True,
            }
            doc_suffix = "documentário" if lang_low.startswith("pt") else "documentary explained"
            q_str = f"ytsearch5:{topic} {doc_suffix}"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(q_str, download=False)
            entries = (info or {}).get("entries") or []
            ranked = sorted(
                [e for e in entries if isinstance(e, dict) and e.get("title")],
                key=lambda x: int(x.get("view_count") or 0),
                reverse=True,
            )
            for item in ranked[:4]:
                t_val = str(item.get("title") or "").strip()
                v_cnt = int(item.get("view_count") or 0)
                ch_name = str(item.get("uploader") or item.get("channel") or "Creator").strip()
                dur_s = int(item.get("duration") or 0)
                if t_val:
                    yt_lines.append(f"  - [{ch_name} | {v_cnt:,} views | {dur_s//60}m{dur_s%60:02d}s]: \"{t_val}\"")
        except Exception as e_yt_res:
            logger.debug(f"[ResearchDossier] YouTube outlier scan skip: {e_yt_res}")

    def _fetch_wikipedia_case_studies() -> None:
        try:
            import html as _html
            from urllib.parse import quote as _urlquote
            headers = {"User-Agent": "AIYouTubeStudio/2.0 (Educational Documentary Research)"}
            wiki_langs = ["en"]
            if lang_low.startswith("pt"):
                wiki_langs = ["pt", "en"]
            elif lang_low.startswith("vi"):
                wiki_langs = ["vi", "en"]

            for w_lang in wiki_langs:
                if len(wiki_lines) >= 3:
                    break
                search_url = f"https://{w_lang}.wikipedia.org/w/api.php"
                params = {
                    "action": "query",
                    "list": "search",
                    "srsearch": f"{topic}",
                    "srlimit": 3,
                    "format": "json",
                }
                resp = _req.get(search_url, params=params, headers=headers, timeout=4.0)
                if resp.status_code != 200:
                    continue
                hits = ((resp.json().get("query") or {}).get("search")) or []
                for idx_h, h in enumerate(hits[:3]):
                    page_title = str(h.get("title") or "").strip()
                    snippet_raw = _html.unescape(re.sub(r"<[^>]+>", "", str(h.get("snippet") or ""))).strip()
                    if idx_h == 0 and page_title:
                        try:
                            sum_url = f"https://{w_lang}.wikipedia.org/api/rest_v1/page/summary/{_urlquote(page_title.replace(' ', '_'))}"
                            s_resp = _req.get(sum_url, headers=headers, timeout=3.0)
                            if s_resp.status_code == 200:
                                ext_txt = str(s_resp.json().get("extract") or "").strip()
                                if len(ext_txt) > 60:
                                    snippet_raw = ext_txt[:420]
                        except Exception:
                            pass
                    if page_title and snippet_raw:
                        wiki_lines.append(f"  - [{w_lang.upper()} Encyclopedia — {page_title}]: {snippet_raw}")
                        if len(wiki_lines) >= 3:
                            break
        except Exception as e_wk:
            logger.debug(f"[ResearchDossier] Wikipedia research skip: {e_wk}")

    with ThreadPoolExecutor(max_workers=2) as res_pool:
        f1 = res_pool.submit(_fetch_yt_outliers)
        f2 = res_pool.submit(_fetch_wikipedia_case_studies)
        try:
            f1.result(timeout=5.5)
        except Exception:
            pass
        try:
            f2.result(timeout=5.5)
        except Exception:
            pass

    dossier_parts = [
        "=== REAL-WORLD RESEARCH DOSSIER (MANDATORY FACTUAL GROUNDING — DO NOT WRITE GENERIC AI CLICHÉS) ==="
    ]
    if yt_lines:
        dossier_parts.append("1. TOP VIRAL YOUTUBE VIDEOS ON THIS TOPIC (STUDY THEIR ANGLES & STAKES):")
        dossier_parts.extend(yt_lines)
    if wiki_lines:
        dossier_parts.append("2. VERIFIED SCIENTIFIC / HISTORICAL / BIOLOGICAL FACTS TO GROUND YOUR CHAPTERS:")
        dossier_parts.extend(wiki_lines)
    dossier_parts.append(
        "3. DEEP ENTITY PROFILING & $1 EVERYDAY SPOKEN IMAGERY RULE: Every chapter MUST analyze the specific entity's real anatomy, "
        "documented historical/forensic case, or physical/behavioral mechanism, and express how it works through fresh, tangible "
        "everyday imagery that ordinary viewers immediately picture and feel — NEVER recite dry laboratory units (PSI, Newtons, kPa, Joules, LD50) "
        "and NEVER recycle stock metaphors across chapters!"
    )
    return "\n".join(dossier_parts)


def _expand_short_scenes_if_needed(
    agent: Any,
    all_scenes: List[Dict[str, Any]],
    doc_title: str,
    topic: str,
    target_lang_display: str,
    lang_instruction: str,
    prose_dna_block: str,
    min_total_words: int,
) -> None:
    """
    Stage 3 Deterministic Mid-Roll Word-Floor Guard:
    Nếu tổng số từ lời thoại của 40 phân cảnh thấp hơn `min_total_words` (nguy cơ hụt mốc 9m15s Mid-Roll Ads),
    chọn tối đa 12 phân cảnh thân bài ngắn nhất và gọi 1 lượt LLM JSON duy nhất để viết sâu thêm chi tiết thực tế
    lên mức 46–54 từ/cảnh (hoàn toàn tự nhiên, không chắp vá câu mẫu).
    """
    cur_words = sum(len(str(s.get("narration") or "").split()) for s in all_scenes)
    if cur_words >= min_total_words:
        return

    deficit = min_total_words - cur_words
    body_candidates = [
        s for s in all_scenes
        if int(s.get("scene_id") or 0) >= 2 and not s.get("overlay_badge")
    ]
    body_candidates.sort(key=lambda x: len(str(x.get("narration") or "").split()))
    need_count = min(len(body_candidates), max(6, min(14, (deficit // 14) + 3)))
    selected = body_candidates[:need_count]
    if not selected:
        return

    scenes_payload = [
        {
            "scene_id": int(s["scene_id"]),
            "context": str(s.get("visual_reasoning") or ""),
            "current_narration": str(s.get("narration") or ""),
        }
        for s in selected
    ]
    expand_prompt = f"""You are the Lead Documentary Script Editor for "{doc_title}" (Topic: "{topic}") in {target_lang_display}.
{lang_instruction}
{prose_dna_block}

Our 40-scene widescreen documentary is currently {deficit} words short of YouTube's 9m30s Mid-Roll target ({cur_words}/{min_total_words} words).
Expand each of the {len(scenes_payload)} scenes below into 3 to 4 vivid, spoken-creator sentences totaling **48 to 56 words per scene** in {target_lang_display}:
- Keep the exact timeline beat, entity, and cliffhanger flow of each scene intact, while adding concrete physical action, sensory detail, or real-world mechanism depth.
- Sound 100% like a natural human YouTuber speaking aloud (use natural contractions in English / colloquial spoken cadence in pt-BR).
- STRICTLY FORBIDDEN: NEVER recite dry textbook units (PSI, Newtons, kPa, Joules, LD50), NEVER start with "Studies show...", and NEVER add Subscribe/comment CTAs.

Scenes to deepen:
{json.dumps(scenes_payload, ensure_ascii=False, indent=2)}

Return ONLY valid JSON:
{{
  "expanded_scenes": [
    {{
      "scene_id": 2,
      "narration": "Expanded 48-56 word spoken documentary narration in {target_lang_display}..."
    }}
  ]
}}"""
    try:
        raw_exp = agent._call_api(expand_prompt, json_mode=True, timeout=38, max_total_seconds=60)
        exp_dict = _extract_json_dict(raw_exp)
        exp_list = exp_dict.get("expanded_scenes") or []
        by_id = {int(s["scene_id"]): s for s in all_scenes if s.get("scene_id")}
        upgraded = 0
        for item in exp_list:
            if not isinstance(item, dict):
                continue
            sid = int(item.get("scene_id") or 0)
            new_narr = str(item.get("narration") or "").strip()
            if sid in by_id and len(new_narr.split()) > len(str(by_id[sid].get("narration") or "").split()) + 4:
                by_id[sid]["narration"] = new_narr
                by_id[sid]["estimated_seconds"] = round(max(15.0, len(new_narr.split()) / 2.65), 1)
                upgraded += 1
        after_words = sum(len(str(s.get("narration") or "").split()) for s in all_scenes)
        logger.info(
            f"📏 [LongForm10Min] Stage-3 Mid-Roll Word-Floor Guard: nâng cấp {upgraded}/{len(selected)} cảnh "
            f"({cur_words} -> {after_words} từ, mục tiêu >= {min_total_words} từ)."
        )
    except Exception as e_exp:
        logger.warning(f"[LongForm10Min] Stage-3 expansion skip: {e_exp}")


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
    Sinh kịch bản Phim Tài Liệu Dài ~9m30s–10m30s (10 Chương / 40 Phân cảnh / ~1,650–1,900 từ, cam kết >= 09:15 Mid-Roll Ads).
    Tuân thủ 100% YAML Niche Profile (`config/niches/*.yaml` qua `niche_manager.py`).
    """
    lang_low = str(language or "en").strip().lower()
    is_pt = lang_low.startswith("pt")
    is_vi = lang_low.startswith("vi")
    ch_word = _chapter_word_for_lang(language)
    chapters_heading = _chapters_heading_for_lang(language)

    niche_profile = get_niche(channel_niche)
    prose_dna_block = build_million_view_showrunner_prose_dna(niche_profile, language=language)
    video_search_block = build_video_search_strategy_prompt_block(niche_profile)
    stock_anchors = list((niche_profile.get("visual_aesthetics") or {}).get("stock_video_keywords") or [])
    default_niche_query = str(stock_anchors[0] if stock_anchors else "dramatic nature documentary").strip()

    real_world_dossier = _mine_real_world_research_dossier(
        topic=topic,
        channel_niche=channel_niche,
        language=language,
    )

    outline_prompt = f"""You are an Executive Showrunner for a 10-Minute Widescreen 16:9 YouTube Documentary on '{niche_profile.get("name", channel_niche)}' (niche id: '{channel_niche}').
Topic: "{topic}"
Target Spoken & Metadata Language: {language} ({target_lang_display})
{lang_instruction}
{director_block}
{real_world_dossier}
{niche_instructions[:2800] if niche_instructions else ""}
{video_search_block}
{channel_memory_block[:1600]}

DESIGN A 10-CHAPTER MASTER DOCUMENTARY OUTLINE (TARGET: 9m30s–10m30s / 40 SCENES / ~1,750 WORDS):
1. `title`: High-CTR Search-Optimized 16:9 Documentary Title in {target_lang_display} (**32–56 characters MAX**, strictly `< 58` chars so YouTube never truncates it, **ZERO `#Shorts` or `#hashtags`**).
   - **RULE 1 (NEVER HIDE THE PRIMARY ENTITY)**: Explicitly name the exact high-search-volume entity/entities or historical/behavioral phenomenon in `title`! NEVER hide the subject behind vague curiosity gaps ("This Creature", "This Place", "Esta Criatura").
   - NEVER use dry physics units (`PSI`, `Newtons`, `kPa`) or generic textbook listicle titles ("10 Simple Tips..."). Follow the YAML `competitor_blueprint.long_form_archetypes` for `{channel_niche}`.
2. `primary_entity`: The core canonical main subject of the documentary in **1 to 3 ENGLISH words** (MUST be English even when Target Language is {target_lang_display}, e.g., `"Jaguar"`, `"Colossal Squid"`, `"Roman Empire"`, `" interrogation suspect"` — NEVER write non-English words here because video search engines require English!).
3. `hook`: Scene 1 Cold-Open Spoken Creator Hook in {target_lang_display} (10–16 words, immediate visual/sensory action or paradox, NEVER repeats `title` verbatim, ZERO "Did you know" / "Você sabia").
4. `thumbnail_text`: MAXIMUM **2 TO 3 ULTRA-PUNCHY ALL-CAPS WORDS** in {target_lang_display} (NO emojis, e.g., `"FATAL TRAP!"`, `"ERRO MORTAL!"`, `"ZERO ESCAPE"`), following the YAML `thumbnail_hook_rule`.
5. `chapters`: Exactly 10 distinct Documentary Chapters (`chapter_id` 1 to 10) following the **DELAYED MAIN-EVENT PAYOFF ARCHITECTURE (ANTI-2-MINUTE-DROPOFF LAW)**:
   - `chapter_title`: MUST be in **{target_lang_display}** starting with `"{ch_word} 1: ..."`, `"{ch_word} 2: ..."`, ..., `"{ch_word} 10: ..."` (NEVER leak English `"CHAPTER"` onto a Portuguese/Vietnamese video!).
   - **CRITICAL RETENTION ARC (NEVER SPOIL THE TITLE'S HEADLINE CLIMAX IN CHAPTERS 1–2)**:
     * **Chapter 1 (`00:00 - 01:00` — COLD-OPEN TEASE & SUSPENDED CLIMAX)**: Plunge straight into the highest-stakes moment of `{topic}`, freeze the outcome as an unresolved Open Loop, and launch the ascending escalation ladder.
     * **Chapters 2 to 7 (`01:00 - 07:00` — STRICT ASCENDING ESCALATION: TIER 1 -> TIER 6)**: Escalate stakes step-by-step from Chapter 2 up through Chapter 7. Each chapter MUST focus on its own distinct challenger, case study, timeline stage, or mechanism so there is ZERO repetition across chapters!
     * **Chapter 8 (`07:00 - 08:00` — THE MAIN EVENT SHOWDOWN: ACT I)**: Unleash the headline confrontation or core mystery promised in `title`.
     * **Chapter 9 (`08:00 - 09:00` — THE MAIN EVENT SHOWDOWN: ACT II COUNTER-TWIST)**: Reveal the decisive anatomical, historical, or behavioral counter-mechanism that seals the outcome.
     * **Chapter 10 (`09:00 - 10:00` — THE FINAL VERDICT & COLD-OPEN RESOLUTION)**: Resolve the exact cliffhanger suspended in Chapter 1 with unforgettable finality.
6. **PER-CHAPTER ENGLISH VISUAL ISOLATION (CRITICAL — PREVENTS CROSS-CHAPTER CLIP CONTAMINATION)**:
   - `chapter_subject_entity`: **1 to 3 ENGLISH words** naming the EXACT physical subject/animal/person/environment featured ON SCREEN in THAT specific chapter (e.g., if Chapter 3 is about Electric Eel in a Portuguese Jaguar gauntlet, `chapter_subject_entity` MUST be `"Electric Eel"` — NOT `"Jaguar"` and NOT Portuguese `"Poraquê"`!).
   - `chapter_master_query`: **3 to 5 ENGLISH words** for real 16:9 documentary footage of `chapter_subject_entity` in action (concrete nouns + physical action, ZERO metaphors, ZERO camera jargon, ZERO non-English words). Every chapter MUST have a distinct visual query!

Return ONLY valid JSON:
{{
  "title": "High-CTR 16:9 Title in {target_lang_display} (32-56 chars, NO #hashtags)",
  "title_variants": ["Variant 1 in {target_lang_display}", "Variant 2 in {target_lang_display}", "Variant 3 in {target_lang_display}"],
  "primary_entity": "1-3 English words naming main subject",
  "hook": "10-16 word Cold-Open Hook in {target_lang_display}",
  "thumbnail_text": "2-3 ALL-CAPS WORDS IN {target_lang_display}",
  "description": "2-3 sentence gripping documentary summary in {target_lang_display}",
  "pinned_comment": "14-24 word 50/50 Two-Faction Debate Question in {target_lang_display} ending with (1) ... or (2) ... 👇",
  "tags": ["tag1", "tag2", "tag3"],
  "hook_search_queries": ["3-5 word English 16:9 action query 1", "3-5 word English 16:9 action query 2", "3-5 word English 16:9 action query 3"],
  "chapters": [
    {{
      "chapter_id": 1,
      "chapter_title": "{ch_word} 1: ...",
      "chapter_premise": "Specific event, real case/species/stage, and physical mechanism covered ONLY in Chapter 1",
      "chapter_subject_entity": "1-3 word English physical subject of Chapter 1",
      "chapter_master_query": "3-5 word English 16:9 documentary search query for Chapter 1"
    }}
  ]
}}"""

    outline_data: Dict[str, Any] = {}
    try:
        raw_outline = agent._call_api(outline_prompt, json_mode=True, timeout=34, max_total_seconds=55)
        outline_data = _extract_json_dict(raw_outline)
    except Exception as e_out:
        logger.warning(f"[LongForm10Min] Stage-1 Outline warning ({e_out}), using structured fallback blueprint.")

    raw_chapters = outline_data.get("chapters") or []
    merged_ch: List[Dict[str, Any]] = []
    default_chapters = build_default_chapter_blueprint(topic, language=language)
    for idx_c in range(TOTAL_CHAPTERS_LONGFORM):
        base_c = dict(default_chapters[idx_c])
        if isinstance(raw_chapters, list) and idx_c < len(raw_chapters) and isinstance(raw_chapters[idx_c], dict):
            rc = raw_chapters[idx_c]
            for k_c, v_c in rc.items():
                if v_c:
                    base_c[k_c] = v_c
            if not rc.get("chapter_master_query"):
                alt_mq = rc.get("master_query") or rc.get("visual_query") or rc.get("search_query")
                if alt_mq:
                    base_c["chapter_master_query"] = str(alt_mq)
            if not rc.get("chapter_subject_entity"):
                alt_se = rc.get("subject_entity") or rc.get("chapter_entity")
                if alt_se:
                    base_c["chapter_subject_entity"] = str(alt_se)
            if not rc.get("chapter_title") and rc.get("title"):
                base_c["chapter_title"] = str(rc.get("title"))
            if not rc.get("chapter_premise") and (rc.get("premise") or rc.get("summary")):
                base_c["chapter_premise"] = str(rc.get("premise") or rc.get("summary"))

        # Chuẩn hóa nhãn chương theo ngôn ngữ kênh (chặn rò rỉ 'CHAPTER X:' tiếng Anh sang kênh pt-BR / vi-VN)
        raw_ct = str(base_c.get("chapter_title") or f"{ch_word} {idx_c + 1}").strip()
        if is_pt:
            raw_ct = re.sub(r"^CHAPTER\s*(\d+)\s*:", r"CAPÍTULO \1:", raw_ct, flags=re.IGNORECASE)
        elif is_vi:
            raw_ct = re.sub(r"^CHAPTER\s*(\d+)\s*:", r"CHƯƠNG \1:", raw_ct, flags=re.IGNORECASE)
        if not re.match(rf"^(?:CHAPTER|CAPÍTULO|CAPITULO|CHƯƠNG|ROUND|RODADA)\s*\d+", raw_ct, flags=re.IGNORECASE):
            raw_ct = f"{ch_word} {idx_c + 1}: {raw_ct}"
        base_c["chapter_title"] = raw_ct
        base_c["chapter_id"] = idx_c + 1

        mq_clean = str(base_c.get("chapter_master_query") or default_niche_query).strip()
        base_c["chapter_master_query"] = mq_clean
        se_clean = str(base_c.get("chapter_subject_entity") or " ".join(mq_clean.split()[:2])).strip()
        base_c["chapter_subject_entity"] = se_clean
        merged_ch.append(base_c)

    chapters_list = merged_ch

    doc_title = str(outline_data.get("title") or topic).strip()
    doc_title = re.sub(r"\s*#\S+", "", doc_title).strip()
    if len(doc_title) > 58:
        doc_title = doc_title[:57].rsplit(" ", 1)[0].rstrip(" ,;:-–—").strip()
        while re.search(
            r"\s+\b(?:o|a|os|as|um|uma|de|do|da|dos|das|no|na|nos|nas|em|por|para|com|sem|que|e|ou|"
            r"the|an|of|in|to|for|with|on|at|from|by|and|or)\s*$",
            doc_title,
            flags=re.IGNORECASE,
        ):
            doc_title = re.sub(
                r"\s+\b(?:o|a|os|as|um|uma|de|do|da|dos|das|no|na|nos|nas|em|por|para|com|sem|que|e|ou|"
                r"the|an|of|in|to|for|with|on|at|from|by|and|or)\s*$",
                "",
                doc_title,
                flags=re.IGNORECASE,
            ).rstrip(" ,;:-–—").strip()

    raw_doc_entity = str(
        outline_data.get("primary_entity")
        or chapters_list[0].get("chapter_subject_entity")
        or " ".join(str(chapters_list[0].get("chapter_master_query") or default_niche_query).split()[:2])
    ).strip()
    doc_entity = raw_doc_entity

    default_hook_by_lang = (
        f"Nos primeiros segundos diante de {topic}, um detalhe invisível muda completamente quem sobrevive."
        if is_pt
        else (
            f"Ngay khoảnh khắc đầu tiên đối mặt với {topic}, một chi tiết ẩn giấu sẽ đảo ngược toàn bộ kết cục."
            if is_vi
            else f"Right at the edge of {topic}, one overlooked detail flips everything you'd expect in seconds."
        )
    )
    doc_hook = str(outline_data.get("hook") or default_hook_by_lang).strip()

    chapter_summary_text = "\n".join(
        f"- Ch.{c.get('chapter_id')}: {c.get('chapter_title')} -> {c.get('chapter_premise')} "
        f"[Subject: {c.get('chapter_subject_entity')} | 16:9 Query: {c.get('chapter_master_query')}]"
        for c in chapters_list
    )

    word_range_scene = "37 to 44" if is_pt else "40 to 47"
    scene1_word_range = "35 to 42" if is_pt else "38 to 45"

    def _expand_chapter_pair(batch_idx: int) -> List[Dict[str, Any]]:
        ch_a = chapters_list[batch_idx * 2]
        ch_b = chapters_list[batch_idx * 2 + 1]
        scenes_in_batch = SCENES_PER_CHAPTER_LONGFORM * 2  # 8 scenes per batch (4 per chapter)
        start_sid = batch_idx * scenes_in_batch + 1
        end_sid = start_sid + scenes_in_batch - 1

        ch_a_id = int(ch_a.get("chapter_id", batch_idx * 2 + 1))
        ch_b_id = int(ch_b.get("chapter_id", batch_idx * 2 + 2))
        ch_a_title = str(ch_a.get("chapter_title") or f"{ch_word} {ch_a_id}")
        ch_b_title = str(ch_b.get("chapter_title") or f"{ch_word} {ch_b_id}")
        ch_a_prem = str(ch_a.get("chapter_premise") or topic)
        ch_b_prem = str(ch_b.get("chapter_premise") or topic)
        ch_a_subj = str(ch_a.get("chapter_subject_entity") or doc_entity).strip()
        ch_b_subj = str(ch_b.get("chapter_subject_entity") or doc_entity).strip()
        ch_a_mq = str(ch_a.get("chapter_master_query") or default_niche_query).strip()
        ch_b_mq = str(ch_b.get("chapter_master_query") or default_niche_query).strip()

        next_ch_after_b = chapters_list[batch_idx * 2 + 2] if (batch_idx * 2 + 2) < len(chapters_list) else None
        next_b_title = str(next_ch_after_b.get("chapter_title")) if next_ch_after_b else "FINAL RESOLUTION"
        next_b_prem = str(next_ch_after_b.get("chapter_premise") or "") if next_ch_after_b else ""

        slot_4_rule_b = (
            f"  * Slot 4 of Chapter {ch_b_id} (Scene {start_sid+7}, CRITICAL RETENTION BRIDGE INTO CHAPTER {ch_b_id+1}): "
            f"NEVER end Scene {start_sid+7} with a closed conclusion! End with an unresolved high-tension cliffhanger pivoting directly into "
            f"'{next_b_title}' ({next_b_prem[:95]}) while reminding the viewer the main showdown is getting closer."
            if next_ch_after_b
            else f"  * Slot 4 of Chapter {ch_b_id} (Scene {start_sid+7}, FINAL DOCUMENTARY PAYOFF): "
            f"Deliver the unforgettable closing resolution that answers the Cold-Open mystery from Scene 1 with zero spoken Subscribe/Comment CTA."
        )

        batch_prompt = f"""You are writing Scenes {start_sid} to {end_sid} (Chapters {ch_a_id} and {ch_b_id}) of a 10-Minute Widescreen 16:9 YouTube Documentary in {target_lang_display}.
Documentary Title: "{doc_title}"
Topic: "{topic}"
Channel Niche: "{channel_niche}"
{lang_instruction}
{real_world_dossier}
{prose_dna_block}
{video_search_block}

FULL 10-CHAPTER DOCUMENTARY ROADMAP (CONTEXT ONLY — STRICT CROSS-BATCH ANTI-REPETITION FIREWALL):
{chapter_summary_text}
- 5 parallel writers are expanding Chapters 1-2, 3-4, 5-6, 7-8, and 9-10 simultaneously.
- You are ONLY writing Chapter {ch_a_id} ("{ch_a_title}") and Chapter {ch_b_id} ("{ch_b_title}").
- NEVER repeat facts, metaphors, or climax reveals assigned to other chapters!

YOUR ASSIGNED 8 SCENES (4 SCENES PER CHAPTER: SCENES {start_sid} TO {end_sid}):
1. Chapter {ch_a_id} ({ch_a_title}) — Scenes {start_sid}, {start_sid+1}, {start_sid+2}, {start_sid+3}:
   - Premise: {ch_a_prem}
   - Canonical English Chapter Subject (`subject_entity`): "{ch_a_subj}"
   - Chapter 16:9 English Master Query: "{ch_a_mq}"
2. Chapter {ch_b_id} ({ch_b_title}) — Scenes {start_sid+4}, {start_sid+5}, {start_sid+6}, {start_sid+7}:
   - Premise: {ch_b_prem}
   - Canonical English Chapter Subject (`subject_entity`): "{ch_b_subj}"
   - Chapter 16:9 English Master Query: "{ch_b_mq}"

MANDATORY WORD COUNT & 4-BEAT CHAPTER STORYTELLING (GUARANTEES 9m30s–10m30s MID-ROLL DURATION):
- {'Scene 1 (`scene_id=1`): Open IMMEDIATELY with the Cold-Open Hook ("' + doc_hook + '") and build 2–3 gripping follow-up sentences totaling **' + scene1_word_range + ' spoken words** (ZERO "Did you know" / "Você sabia").' if start_sid == 1 else f'Every scene from Scene {start_sid} to Scene {end_sid} MUST contain 3 to 4 complete spoken sentences totaling **{word_range_scene} WORDS PER SCENE** (~15 seconds per scene, so 40 scenes = ~1,800 words = 10 full minutes).'}
- Every body scene MUST contain **{word_range_scene} spoken words** in {target_lang_display}. NEVER write short 20-word summaries and NEVER split one sentence across two scenes!
- 4-BEAT CHAPTER ARC (4 SCENES PER CHAPTER):
  * Slot 1 (Scenes {start_sid} & {start_sid+4}): **Immediate In-Medias-Res Scene-Setter ("Show, Don't Tell")** — plunge straight into the real encounter, historical moment, or behavioral situation of that chapter (NEVER open with "Studies show..." or dry outline labels).
  * Slot 2 (Scenes {start_sid+1} & {start_sid+5}): **Physical Escalation & Sensory Scale** — zoom into the concrete action, anatomy, environment, or tactical move as tension rises.
  * Slot 3 (Scenes {start_sid+2} & {start_sid+6}): **Hidden Mechanism & Myth-Flip** — reveal WHY this happens using crystal-clear `$1` everyday spoken comparisons (STRICTLY FORBIDDEN: NEVER recite textbook lab units like `PSI`, `Newtons`, `kPa`, `Joules`, `LD50`, or stiff academic essay jargon!).
  * Slot 4 of Chapter {ch_a_id} (Scene {start_sid+3}): **Turning Point + Open-Loop Cliffhanger into Chapter {ch_b_id}** ("{ch_b_title}").
{slot_4_rule_b}
- **ZERO SPOKEN OUTRO CTA**: Never say "Subscribe", "Inscreva-se", "Like", or "Comment" in `narration`.
- **PER-SCENE ENGLISH `subject_entity` & `search_queries` (CRITICAL FOR 16:9 VIDEO FETCHER)**:
  * `subject_entity`, `visual_keyword`, `stock_query`, and `search_queries` MUST ALWAYS BE **100% ENGLISH** (even when `narration` is in {target_lang_display})!
  * Lock Scenes {start_sid}–{start_sid+3} to Chapter {ch_a_id}'s English subject (`"{ch_a_subj}"`) and Scenes {start_sid+4}–{start_sid+7} to Chapter {ch_b_id}'s English subject (`"{ch_b_subj}"`). NEVER copy Chapter 1's animal/entity into another chapter if that chapter features a different challenger/subject!
  * Give each of the 4 scenes in a chapter a distinct visual angle (Slot 1: master action, Slot 2: close-up detail, Slot 3: wide environment/tracking, Slot 4: peak clash/reaction) and 3 concrete 3–5 word English `search_queries` (real filmable nouns + natural environment/behavior verbs; ZERO metaphors, ZERO cartoons/diagrams/icons, and NEVER pair wildlife/non-human subjects with human gym/anatomy phrases like `shoulder muscles`, `biceps`, `workout`, or `looking back`, which cause stock engines to return human fitness clips!).

Return ONLY valid JSON with key `"scenes"` containing all 8 scene objects (`scene_id` {start_sid} through {end_sid}):
{{
  "scenes": [
    {{
      "scene_id": {start_sid},
      "subject_entity": "{ch_a_subj}",
      "visual_reasoning": "{ch_a_title} — Slot 1 opening action",
      "narration": "3 to 4 complete spoken documentary sentences in {target_lang_display} totaling {word_range_scene} words...",
      "visual_keyword": "{ch_a_mq}",
      "visual_prompt": "Cinematic 16:9 widescreen documentary footage of {ch_a_mq}, natural lighting, sharp focus",
      "stock_query": "{' '.join(ch_a_mq.split()[:3])}",
      "search_queries": [
        "{ch_a_mq}",
        "{ch_a_subj} close up action",
        "{ch_a_subj} natural habitat documentary"
      ],
      "media_type": "video",
      "camera_motion": "zoom_in",
      "sfx_cue": "none",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "{f'[ // {ch_a_title.upper()} // ]'}",
      "estimated_seconds": 15.5
    }}
  ]
}}"""

        slot_angle_suffixes = [
            "",
            "close up action",
            "wide habitat footage",
            "dramatic encounter",
        ]
        for attempt_b in range(3):
            try:
                raw_b = agent._call_api(batch_prompt, json_mode=True, timeout=48, max_total_seconds=80)
                b_dict = _extract_json_dict(raw_b)
                b_scenes = b_dict.get("scenes") or []
                if isinstance(b_scenes, list) and len(b_scenes) >= 6:
                    normalized_b: List[Dict[str, Any]] = []
                    for idx_local in range(scenes_in_batch):
                        target_id = start_sid + idx_local
                        ch_cur = ch_a if idx_local < SCENES_PER_CHAPTER_LONGFORM else ch_b
                        slot_in_ch = idx_local % SCENES_PER_CHAPTER_LONGFORM
                        if idx_local < len(b_scenes) and isinstance(b_scenes[idx_local], dict):
                            sc_item = dict(b_scenes[idx_local])
                        else:
                            sc_item = {}
                        sc_item["scene_id"] = target_id
                        ch_title_cur = str(ch_cur.get("chapter_title") or f"{ch_word} {ch_cur.get('chapter_id')}").strip()
                        ch_subj_cur = str(ch_cur.get("chapter_subject_entity") or doc_entity).strip()
                        master_q_cur = str(ch_cur.get("chapter_master_query") or default_niche_query).strip()

                        cur_vr = str(sc_item.get("visual_reasoning") or "").strip()
                        if ch_title_cur.lower() not in cur_vr.lower():
                            sc_item["visual_reasoning"] = f"{ch_title_cur} — {cur_vr}" if cur_vr else ch_title_cur

                        narr_txt = str(sc_item.get("narration") or "").strip()
                        if target_id == 1 and len(narr_txt.split()) < 10:
                            narr_txt = doc_hook
                        elif len(narr_txt.split()) < 8:
                            extra_premise = str(ch_cur.get("chapter_premise") or "").strip().rstrip(".")
                            if extra_premise:
                                narr_txt = f"{extra_premise}."
                        sc_item["narration"] = narr_txt

                        # Bảo toàn subject_entity tiếng Anh cấp chương/cảnh để chặn nhiễm chéo thực thể giữa 10 Chương
                        raw_sc_subj = str(sc_item.get("subject_entity") or "").strip()
                        if not raw_sc_subj or re.search(r"[ãõçêôáéíóúàươđ]", raw_sc_subj.lower()):
                            raw_sc_subj = ch_subj_cur
                        sc_item["subject_entity"] = raw_sc_subj

                        cur_vk = str(sc_item.get("visual_keyword") or "").strip()
                        cur_sq = str(sc_item.get("stock_query") or "").strip()
                        if slot_in_ch == 0 or not cur_vk:
                            sc_item["visual_keyword"] = (
                                master_q_cur
                                if slot_in_ch == 0
                                else f"{master_q_cur} {slot_angle_suffixes[slot_in_ch]}".strip()
                            )
                        elif cur_vk.lower() == master_q_cur.lower() and slot_in_ch > 0:
                            sc_item["visual_keyword"] = f"{cur_vk} {slot_angle_suffixes[slot_in_ch]}".strip()
                        else:
                            sc_item["visual_keyword"] = cur_vk

                        if slot_in_ch == 0 or not cur_sq:
                            sc_item["stock_query"] = " ".join(sc_item["visual_keyword"].split()[:4])
                        else:
                            sc_item["stock_query"] = cur_sq

                        raw_sqs = sc_item.get("search_queries") if isinstance(sc_item.get("search_queries"), list) else []
                        clean_sqs: List[str] = []
                        for q_c in [*raw_sqs, sc_item["visual_keyword"], master_q_cur, f"{raw_sc_subj} documentary footage"]:
                            q_s = str(q_c or "").strip()
                            if q_s and q_s.lower() not in {x.lower() for x in clean_sqs}:
                                clean_sqs.append(q_s)
                        sc_item["search_queries"] = clean_sqs[:4]

                        if not sc_item.get("visual_prompt"):
                            sc_item["visual_prompt"] = f"Cinematic 16:9 widescreen documentary shot of {sc_item['visual_keyword']}"
                        sc_item["media_type"] = "video"
                        if slot_in_ch == 0:
                            sc_item["overlay_badge"] = f"[ // {ch_title_cur.upper()} // ]"
                        else:
                            sc_item["overlay_badge"] = None
                        sc_item["estimated_seconds"] = round(max(14.5, len(narr_txt.split()) / 2.65), 1)
                        normalized_b.append(sc_item)
                    return normalized_b
            except Exception as e_b:
                logger.warning(f"[LongForm10Min] Batch {batch_idx+1}/5 attempt {attempt_b+1} warning: {e_b}")

        # Fallback an toàn theo đúng ngôn ngữ kênh & chủ đề nếu cả 3 lần gọi mạng đều lỗi
        fallback_b: List[Dict[str, Any]] = []
        for idx_local in range(scenes_in_batch):
            target_id = start_sid + idx_local
            ch_cur = ch_a if idx_local < SCENES_PER_CHAPTER_LONGFORM else ch_b
            slot_in_ch = idx_local % SCENES_PER_CHAPTER_LONGFORM
            premise_clean = str(ch_cur.get("chapter_premise") or topic).strip().rstrip(".")
            m_query = str(ch_cur.get("chapter_master_query") or default_niche_query).strip()
            c_subj = str(ch_cur.get("chapter_subject_entity") or doc_entity).strip()
            if is_pt:
                narr_fb = (
                    f"{premise_clean}. Nesse exato ponto da disputa, cada segundo muda completamente o rumo do confronto, "
                    f"porque um detalhe físico invisível entra em ação rápido demais pra qualquer reação comum conseguir acompanhar a tempo."
                )
            elif is_vi:
                narr_fb = (
                    f"{premise_clean}. Ngay tại thời khắc quyết định đó, từng phần mười giây đều xoay chuyển hoàn toàn cục diện, "
                    f"khi cơ chế ẩn giấu lập tức kích hoạt nhanh hơn mọi phản xạ thông thường có thể kịp nhận ra."
                )
            else:
                narr_fb = (
                    f"{premise_clean}. Right at this turning point, every split second reshapes the entire confrontation on the spot, "
                    f"because a hidden physical mechanism locks into place faster than ordinary reflexes can ever hope to counter."
                )
            ch_title_fb = str(ch_cur.get("chapter_title") or f"{ch_word} {ch_cur.get('chapter_id', 1)}").strip().upper()
            vk_fb = m_query if slot_in_ch == 0 else f"{m_query} {slot_angle_suffixes[slot_in_ch]}".strip()
            fallback_b.append({
                "scene_id": target_id,
                "subject_entity": c_subj,
                "visual_reasoning": f"{ch_cur.get('chapter_title')} documentary beat {slot_in_ch + 1}",
                "narration": narr_fb,
                "visual_keyword": vk_fb,
                "visual_prompt": f"Cinematic 16:9 widescreen documentary shot of {vk_fb}",
                "stock_query": " ".join(m_query.split()[:3]),
                "search_queries": [vk_fb, m_query, f"{c_subj} documentary"],
                "media_type": "video",
                "camera_motion": "zoom_in" if idx_local % 2 == 0 else "pan_right",
                "sfx_cue": "none",
                "highlight_words": [],
                "overlay_badge": f"[ // {ch_title_fb} // ]" if slot_in_ch == 0 else None,
                "estimated_seconds": 15.5,
            })
        return fallback_b

    logger.info(
        f"🎬 [LongForm10Min] Đang chạy song song 5 Batch LLM viết 10 Chương "
        f"({TOTAL_SCENES_LONGFORM} phân cảnh / ~1,750 từ / >= 9m15s Mid-Roll Ads)..."
    )
    with ThreadPoolExecutor(max_workers=5) as pool:
        batch_results = list(pool.map(_expand_chapter_pair, range(5)))

    all_scenes: List[Dict[str, Any]] = []
    for b_list in batch_results:
        all_scenes.extend(b_list)

    for idx_s, sc in enumerate(all_scenes, start=1):
        sc["scene_id"] = idx_s
        ch_i0 = min(len(chapters_list) - 1, (idx_s - 1) // SCENES_PER_CHAPTER_LONGFORM)
        if (idx_s - 1) % SCENES_PER_CHAPTER_LONGFORM == 0:
            sc["chapter_title"] = str(chapters_list[ch_i0].get("chapter_title") or "").strip()
        else:
            sc["overlay_badge"] = None
            sc["chapter_title"] = ""

    # Stage 3: Đảm bảo tổng số từ đạt sàn Mid-Roll (>= 1,430 từ PT/VI, >= 1,520 từ EN -> chuẩn ~9m30s–10m30s)
    min_words_floor = 1430 if (is_pt or is_vi) else 1520
    _expand_short_scenes_if_needed(
        agent=agent,
        all_scenes=all_scenes,
        doc_title=doc_title,
        topic=topic,
        target_lang_display=target_lang_display,
        lang_instruction=lang_instruction,
        prose_dna_block=prose_dna_block,
        min_total_words=min_words_floor,
    )

    full_narr = " ".join(str(s.get("narration") or "").strip() for s in all_scenes if s.get("narration"))
    total_w = len(full_narr.split())
    logger.info(
        f"✅ [LongForm10Min] Hoàn tất kịch bản Phim Tài Liệu 10 Chương: "
        f"{len(all_scenes)} phân cảnh | Tổng số từ: {total_w} từ (~{total_w / 2.65:.0f}s / ~{total_w / 159.0:.1f} phút)!"
    )

    # Tính toán bảng Mốc Thời Gian 10 Chương sơ bộ chuẩn YouTube Chapters (sẽ được `render_nodes.py` đồng bộ chính xác từng giây theo TTS `audio_duration_ms`)
    intro_label = "Introdução" if is_pt else ("Mở đầu" if is_vi else "Intro")
    chapter_timestamps_lines: List[str] = []
    cum_sec = 0.0
    for ch_idx_0 in range(TOTAL_CHAPTERS_LONGFORM):
        ch_meta = chapters_list[ch_idx_0]
        mm = int(cum_sec // 60)
        ss = int(cum_sec % 60)
        raw_ch_t = str(ch_meta.get("chapter_title") or f"{ch_word} {ch_idx_0 + 1}").strip()
        clean_ch_label = re.sub(
            r"^(?:CHAPTER|CAPÍTULO|CAPITULO|CHƯƠNG|ACT|PART|HỒI)\s*\d+\s*[:\-–—]\s*",
            "",
            raw_ch_t,
            flags=re.IGNORECASE,
        ).strip() or raw_ch_t
        if ch_idx_0 == 0:
            chapter_timestamps_lines.append(f"0:00 {intro_label} — {clean_ch_label}")
        else:
            chapter_timestamps_lines.append(f"{mm}:{ss:02d} {clean_ch_label}")
        ch_scenes = all_scenes[
            ch_idx_0 * SCENES_PER_CHAPTER_LONGFORM : (ch_idx_0 + 1) * SCENES_PER_CHAPTER_LONGFORM
        ]
        ch_dur = sum(float(s.get("estimated_seconds") or 15.0) for s in ch_scenes)
        cum_sec += max(52.0, ch_dur)

    timestamps_block = "\n".join(chapter_timestamps_lines)
    default_desc_summary = (
        f"Documentário completo em widescreen 16:9 sobre {doc_title}."
        if is_pt
        else (
            f"Phim tài liệu chuyên sâu khung ngang 16:9 về {doc_title}."
            if is_vi
            else f"Full 10-Minute Widescreen Documentary on {doc_title}."
        )
    )
    base_desc = str(outline_data.get("description") or default_desc_summary).strip()
    base_desc = re.sub(r"#shorts\b", "", base_desc, flags=re.IGNORECASE).strip()

    entity_hashtag = re.sub(r"[^a-zA-Z0-9À-ÿ]", "", doc_entity.title()) or "Documentary"
    niche_Clean_tag = re.sub(r"[^a-zA-Z0-9]", "", str(niche_profile.get("id") or channel_niche).title()) or "Science"
    doc_hashtag = "#Documentario" if is_pt else "#Documentary"
    if "0:00" not in base_desc and "00:00" not in base_desc:
        full_seo_description = (
            f"{doc_hook}\n\n"
            f"{base_desc}\n\n"
            f"{chapters_heading}\n"
            f"{timestamps_block}\n\n"
            f"{doc_hashtag} #{entity_hashtag} #{niche_Clean_tag}"
        )
    else:
        full_seo_description = base_desc

    raw_tags = [
        str(t).strip()
        for t in (outline_data.get("tags") or [])
        if str(t).strip() and "short" not in str(t).lower()
    ]
    for ch_m in chapters_list[:8]:
        mq_tag = str(ch_m.get("chapter_master_query") or "").strip().lower()
        if mq_tag and mq_tag not in raw_tags:
            raw_tags.append(mq_tag)
    doc_word_tag = "documentário" if is_pt else "documentary"
    for base_t in [topic.lower(), doc_entity.lower(), f"{doc_entity.lower()} {doc_word_tag}"]:
        if base_t and base_t not in raw_tags:
            raw_tags.append(base_t)

    hook_queries_default = list(all_scenes[0].get("search_queries") or [chapters_list[0].get("chapter_master_query") or default_niche_query])
    raw_thumb = str(outline_data.get("thumbnail_text") or "").strip()
    if not raw_thumb:
        raw_thumb = "VERDADE OCULTA!" if is_pt else ("SỰ THẬT ẨN GIẤU!" if is_vi else "HIDDEN TRUTH!")

    assembled_json = {
        "video_format": "long",
        "title": doc_title,
        "title_variants": outline_data.get("title_variants") or [doc_title],
        "primary_entity": doc_entity,
        "hook": str(all_scenes[0].get("narration") or doc_hook),
        "hook_variants": [doc_hook],
        "hook_visual_mode": "video",
        "is_abstract_keyword": False,
        "climax_scene_id": 32,
        "hook_search_queries": outline_data.get("hook_search_queries") or hook_queries_default[:3],
        "full_narration": full_narr,
        "thumbnail_text": raw_thumb,
        "scenes": all_scenes,
        "tags": raw_tags[:18],
        "description": full_seo_description,
        "pinned_comment": str(outline_data.get("pinned_comment") or "").strip(),
        "viral_formula": "10-Chapter 40-Scene Widescreen Documentary | Multi-Source YouTube 16:9 Chapter Pool | Delayed Main-Event Payoff",
    }

    raw_assembled_str = json.dumps(assembled_json, ensure_ascii=False)
    contract = agent._parse_json_script(
        raw_assembled_str,
        channel_niche=channel_niche,
        topic=topic,
        video_format="long",
    )
    return contract
