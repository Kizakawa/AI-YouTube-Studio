"""
app.services.niche_manager — Hệ thống quản lý Hồ Sơ Ngách Kênh Chuyên Sâu (Niche Intelligence Profiles).
Nạp, kiểm tra, đồng bộ và cung cấp các quy tắc kịch bản, thị giác, âm thanh và phụ đề cho từng kênh YouTube.
"""
import os
import re
import yaml
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

# Cache in-memory
_cached_niches: Optional[Dict[str, Dict[str, Any]]] = None
_cached_video_metadata_regexes: Dict[str, Tuple[Optional[re.Pattern], Optional[re.Pattern], bool]] = {}

# Alias mapping cho các từ khóa ngách cũ
NICHE_ALIASES: Dict[str, str] = {
    "psychology": "psychology",
    "tam_ly": "psychology",
    "tam_ly_hoc": "psychology",
    "history": "history_mystery",
    "mystery": "history_mystery",
    "history_mystery": "history_mystery",
    "lich_su": "history_mystery",
    "alternate_history": "alternate_history",
    "what_if": "alternate_history",
    "whatif": "alternate_history",
    "alt_history": "alternate_history",
    "lich_su_gia_dinh": "alternate_history",
    "deep_sea": "deep_sea_earth_mysteries",
    "deep_sea_earth_mysteries": "deep_sea_earth_mysteries",
    "ocean": "deep_sea_earth_mysteries",
    "ocean_mysteries": "deep_sea_earth_mysteries",
    "thalassophobia": "deep_sea_earth_mysteries",
    "bien_sau": "deep_sea_earth_mysteries",
    "mysteries": "deep_sea_earth_mysteries",
    "finance": "finance_wealth",
    "wealth": "finance_wealth",
    "money": "finance_wealth",
    "finance_wealth": "finance_wealth",
    "tai_chinh": "finance_wealth",
    "tech": "scifi_future_tech",
    "science": "scifi_future_tech",
    "ai": "scifi_future_tech",
    "scifi": "scifi_future_tech",
    "scifi_future_tech": "scifi_future_tech",
    "cong_nghe": "scifi_future_tech",
    "philosophy": "philosophy_stoicism",
    "stoic": "philosophy_stoicism",
    "stoicism": "philosophy_stoicism",
    "philosophy_stoicism": "philosophy_stoicism",
    "triet_hoc": "philosophy_stoicism",
    "horror": "horror_urban_legends",
    "creepy": "horror_urban_legends",
    "creepypasta": "horror_urban_legends",
    "kinh_di": "horror_urban_legends",
    "horror_urban_legends": "horror_urban_legends",
    "health": "health_biohacking",
    "biohack": "health_biohacking",
    "biohacking": "health_biohacking",
    "health_biohacking": "health_biohacking",
    "suc_khoe": "health_biohacking",
    "luxury": "luxury_motivation",
    "motivation": "luxury_motivation",
    "discipline": "luxury_motivation",
    "luxury_motivation": "luxury_motivation",
    "dong_luc": "luxury_motivation",
    "animal": "animal_rescue",
    "animals": "animal_rescue",
    "animal_rescue": "animal_rescue",
    "rescue": "animal_rescue",
    "pet": "animal_rescue",
    "pets": "animal_rescue",
    "pet_rescue": "animal_rescue",
    "paws": "animal_rescue",
    "yeu_dong_vat": "animal_rescue",
    "giai_cuu_dong_vat": "animal_rescue",
    "thu_cung": "animal_rescue",
    "wildlife": "wildlife_predators",
    "wild": "wildlife_predators",
    "wildlife_predators": "wildlife_predators",
    "predator": "wildlife_predators",
    "predators": "wildlife_predators",
    "apex_predator": "wildlife_predators",
    "nature": "wildlife_predators",
    "dong_vat_san_moi": "wildlife_predators",
    "dong_vat_hoang_da": "wildlife_predators",
    "san_moi": "wildlife_predators",
    "satirical_psychology_animation": "satirical_psychology_animation",
    "tam_ly_cham_biem": "satirical_psychology_animation",
    "tam_ly_doi_thuong": "satirical_psychology_animation",
    "hoat_hinh_tam_ly": "satirical_psychology_animation",
    "healing_animation": "satirical_psychology_animation",
    "people_pleaser": "satirical_psychology_animation",
    "comic_tam_ly": "satirical_psychology_animation",
    "general": "general",
    "auto": "general",
    "tong_hop": "general",
}


def get_niches_dir() -> Path:
    """Trả về đường dẫn thư mục config/niches."""
    niches_dir = Path(settings.BASE_DIR) / "config" / "niches"
    niches_dir.mkdir(parents=True, exist_ok=True)
    return niches_dir


def load_all_niches(force_reload: bool = False) -> List[Dict[str, Any]]:
    """
    Quét và đọc toàn bộ file *.yaml trong config/niches.
    Trả về danh sách các Niche Profile.
    """
    global _cached_niches, _cached_video_metadata_regexes
    if _cached_niches is not None and not force_reload:
        return list(_cached_niches.values())
    _cached_video_metadata_regexes.clear()

    niches_dir = get_niches_dir()
    profiles: Dict[str, Dict[str, Any]] = {}

    for file_path in niches_dir.glob("*.yaml"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "id" in data:
                    nid = data["id"].strip().lower()
                    data["_file_name"] = file_path.name
                    profiles[nid] = data
        except Exception as e:
            logger.error(f"[NicheManager] Lỗi đọc file {file_path.name}: {e}")

    _cached_niches = profiles
    logger.info(f"[NicheManager] Đã nạp {len(profiles)} hồ sơ ngách từ {niches_dir}")
    return list(profiles.values())


def get_niche(niche_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Lấy thông tin chi tiết một Niche Profile theo ID.
    Hỗ trợ alias lookup và fallback về 'general' nếu không tìm thấy.
    """
    global _cached_niches
    if _cached_niches is None:
        load_all_niches()

    if not niche_id:
        niche_id = "general"

    raw_id = str(niche_id).strip().lower()
    clean_id = NICHE_ALIASES.get(raw_id, raw_id)

    if _cached_niches and clean_id in _cached_niches:
        return _cached_niches[clean_id]

    # Thử tìm theo substring
    if _cached_niches:
        for k, v in _cached_niches.items():
            if k in clean_id or clean_id in k:
                return v

    # Fallback về general
    if _cached_niches and "general" in _cached_niches:
        return _cached_niches["general"]

    # Fallback rỗng an toàn nếu chưa có gì
    return {
        "id": "general",
        "name": "Tổng Hợp",
        "badge": "🌐 Tổng Hợp",
        "icon": "🌐",
        "script_blueprint": {
            "tone_of_voice": "Cuốn hút, tự nhiên, kịch tính vừa phải.",
            "hook_formulas": ["Curiosity Gap", "Shock Fact"],
            "forbidden_words": ["Chào mừng các bạn", "Trong video này"],
            "cta_templates": ["Nhấn thích và theo dõi kênh..."],
        },
        "visual_aesthetics": {
            "art_style": "Dynamic High-Definition Realism",
            "master_prompt_prefix": "8k vertical film still, 35mm photography",
            "master_prompt_suffix": "photorealistic 9:16 vertical composition, no watermark, no text",
            "negative_prompts": "blurry, low quality, cartoon, watermark, text",
        },
        "subtitles": {
            "primary_color": "#FFFFFF",
            "highlight_color": "#FFE600",
            "outline_color": "#000000",
            "font_family": "Montserrat, sans-serif",
            "font_size": 40,
        },
        "audio": {
            "preferred_voice_en": "en-US-ChristopherNeural",
            "preferred_voice_vi": "vi-VN-NamMinhNeural",
            "bgm_vibe": "dynamic upbeat cinematic",
        }
    }


def get_channel_niche(channel_id: str) -> Dict[str, Any]:
    """
    Lấy hồ sơ ngách chuyên sâu cho một kênh YouTube cụ thể (hỗ trợ cả alias như channel_maya, ucsrjiil67qxidtxt70z-iwa).
    Đọc từ channels.yaml để tìm niche ID, sau đó nạp profile.
    """
    from app.services.channel_manager import get_channel
    ch = get_channel(channel_id)
    if not ch and channel_id:
        try:
            from app.services.smart_scheduler import resolve_channel_family
            for alias_id in resolve_channel_family(channel_id):
                ch = get_channel(alias_id)
                if ch:
                    break
        except Exception:
            pass
    if not ch:
        return get_niche("general")

    niche_key = ch.get("niche") or "general"
    profile = get_niche(niche_key).copy()

    # Áp dụng preferred_voice từ channel nếu có
    if ch.get("preferred_voice"):
        audio_cfg = profile.setdefault("audio", {})
        if str(ch.get("language", "en")).startswith("vi"):
            audio_cfg["preferred_voice_vi"] = ch["preferred_voice"]
        else:
            audio_cfg["preferred_voice_en"] = ch["preferred_voice"]

    profile["_channel_id"] = channel_id
    profile["_channel_name"] = ch.get("name", channel_id)
    return profile


def save_niche(niche_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Lưu hoặc cập nhật một Niche Profile vào file YAML.
    """
    global _cached_niches
    clean_id = str(niche_data.get("id", "")).strip().lower().replace(" ", "_")
    clean_id = re.sub(r'[^a-z0-9_]', '', clean_id)
    if not clean_id:
        return False, "ID của hồ sơ ngách không hợp lệ"

    niche_data["id"] = clean_id
    niches_dir = get_niches_dir()
    file_path = niches_dir / f"{clean_id}.yaml"

    try:
        # Xóa trường nội bộ trước khi ghi
        save_data = {k: v for k, v in niche_data.items() if not k.startswith("_")}
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(save_data, f, allow_unicode=True, sort_keys=False, indent=2)

        load_all_niches(force_reload=True)
        return True, f"Đã lưu hồ sơ ngách '{clean_id}' thành công"
    except Exception as e:
        logger.error(f"[NicheManager] Lỗi ghi file {file_path}: {e}")
        return False, f"Lỗi ghi file: {str(e)}"


def delete_niche(niche_id: str) -> Tuple[bool, str]:
    """
    Xóa một hồ sơ ngách tùy biến (không cho phép xóa 'general').
    """
    global _cached_niches
    clean_id = str(niche_id).strip().lower()
    if clean_id in ("general", "psychology", "history_mystery", "finance_wealth"):
        return False, f"Không thể xóa hồ sơ ngách mặc định cốt lõi '{clean_id}'"

    niches_dir = get_niches_dir()
    file_path = niches_dir / f"{clean_id}.yaml"
    if not file_path.exists():
        return False, f"Không tìm thấy hồ sơ '{clean_id}'"

    try:
        file_path.unlink()
        load_all_niches(force_reload=True)
        return True, f"Đã xóa hồ sơ ngách '{clean_id}'"
    except Exception as e:
        return False, f"Lỗi xóa file: {str(e)}"


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT INJECTION & STYLE CONVERTERS
# ─────────────────────────────────────────────────────────────────────────────

def build_million_view_showrunner_prose_dna(niche_id_or_profile: Any, language: str = "en") -> str:
    """
    Đọc trực tiếp khối `showrunner_prose_dna` và `controversy_comment_bait_engine` từ file YAML (`config/niches/*.yaml`)
    để cung cấp cho LLM Showrunner & Director Node quyết định cách viết kịch bản và gài điểm tranh cãi kích bình luận.
    Tuyệt đối không hardcode if/elif theo từng ngách trong Python!
    """
    if isinstance(niche_id_or_profile, dict):
        profile = niche_id_or_profile
    else:
        profile = get_niche(str(niche_id_or_profile or "general"))

    blueprint = profile.get("script_blueprint", {}) or {}
    comp_bp = blueprint.get("competitor_blueprint", {}) or {}
    prose_dna = blueprint.get("showrunner_prose_dna", {}) or {}
    bait_engine = blueprint.get("controversy_comment_bait_engine", {}) or {}
    is_pt = str(language or "en").strip().lower().startswith("pt")

    bench_label = comp_bp.get("benchmark_channel") or profile.get("name", "Million-View Benchmark Channels")
    persona = prose_dna.get("persona_and_voice") or blueprint.get("tone_of_voice", "Elite Documentary Showrunner")
    habits = prose_dna.get("writing_habits") or []
    gen_prose = (get_niche("general").get("script_blueprint") or {}).get("showrunner_prose_dna") or {}
    analogy_rules = prose_dna.get("visceral_analogy_rules") or gen_prose.get("visceral_analogy_rules") or []
    bad_ex = prose_dna.get("bad_ai_slop_example") or ""
    good_ex = (prose_dna.get("good_pt_br_example") if is_pt and prose_dna.get("good_pt_br_example") else prose_dna.get("good_million_view_example")) or ""
    bait_phil = bait_engine.get("philosophy") or (
        "ORGANIC MYTH-FLIPPING OVER ROBOTIC HOT-TAKES: Spark curiosity and comment debates naturally by shattering a popular assumption "
        "('You'd probably think X... But here's what actually happens' / 'Você pensaria que... Só que na prática...') "
        "paired with a sharp 50/50 counterfactual pinned comment—NEVER break story immersion with robotic 'X is overrated/inferior' thesis lines!"
    )
    bait_examples = bait_engine.get("bait_examples") or []

    lines: List[str] = [
        f"8. MILLION-VIEW HUMAN CREATOR SPOKEN VOICE, ACTION-FIRST FLOW & EVERYDAY CLARITY (FROM YAML — BENCHMARK: {bench_label}):",
        "   - 🗣️ THE KITCHEN-TABLE READ-ALOUD RULE (SOUND LIKE A REAL YOUTUBER, NEVER AN AI ESSAY OR HYPE BOT): "
        "Write every line as if a charismatic human creator (Zack D. Films, Casual Geographic, Charisma on Command, The Dodo, Fatos Desconhecidos) "
        "is telling one friend an unbelievable true story. Use $1 everyday spoken words instead of $10 academic, clinical, or poetic words!",
        "   - 💬 NATURAL CONTRACTIONS & ACTION-FIRST FLOW (ZERO RUBBER-STAMPED HYPE FILLER): "
        "In English, ALWAYS use natural spoken contractions ('it's', 'doesn't', 'can't', 'you'd', 'that's', 'won't', 'they're', 'didn't', 'would've', 'hadn't' — NEVER write 7-8 uncontracted Wikipedia textbook sentences!). "
        "Drop straight into concrete crisis/action and crisp contrasts ('Most predators go for the throat. Not this one.', 'And that's actually on purpose.', 'You'd think... except...'). "
        "STRICTLY BAN rubber-stamped AI hype transitions: NEVER write 'But here's the crazy part:', 'Here's the crazy part:', 'You see, it was...', or start multiple scenes with 'You see,'. "
        "In Brazilian Portuguese (pt-BR), speak colloquial Brazilian YouTuber Portuguese ('pra', 'pro', 'tá', 'bicho', 'na hora', 'num piscar de olhos', 'só que', 'em vez de') — NEVER write stiff TV-documentary dub Portuguese, formal enclitics ('imobilizá-lo', 'matá-lo'), clinical textbook lines ('desligando o sistema nervoso', 'mordida precisa'), school-essay fillers ('Muita gente acha que', 'No fim das contas'), or fake moralizing nature proverbs ('O Pantanal pune quem falha', 'a natureza não perdoa', 'o rei da floresta reina')!",
        "   - 🚫 STRICT ANTI-AI ESSAY, ANTI-WIKIPEDIA SPOILER, ANTI-CLINICAL & ANTI-CLICHÉ BAN: "
        "NEVER start Scene 1 with a dry Wikipedia newspaper summary that spoils the final resolution ('In October 1962, Officer X blocked Y', 'In 1961, a switch prevented an explosion') — drop the viewer inside the split-second crisis BEFORE the outcome is decided! "
        "NEVER start sentences with front-loaded participial essay clauses ('Equipped with...', 'Armed with...', 'Boasting...', 'Lurking in...', 'Equipado com...', 'Ostentando...'). "
        "NEVER drop clinical, chemistry-hand-waving, bureaucratic, pop-psych, or documentary clichés into spoken Shorts ('amygdala', 'prefrontal cortex', 'social conditioning', 'calculated strike', 'self-image', 'test your submission', 'designed to keep you guessing', 'reveal their game', 'Under ... rules, ... required unanimous agreement', 'held absolute veto power', 'averting a nuclear holocaust', 'History books call it...', 'We're all here today only because...', 'air was thick with sweat', 'silent vigil', 'special molecules', 'organic molecules', 'permeates', 'structural differential', 'elapid', 'the hunter becomes the meal', 'the predator becomes the prey', 'Most people assume...'). "
        "NEVER force cheesy literary similes ('acting like...', 'vibrating like a taut string', 'gentle embrace', 'calls the abyss home') — use a quick everyday comparison ONLY when explaining an invisible macroscopic physical mechanism, otherwise let concrete verbs do the work!",
        "   - 🧠 ENTITY-FIRST MECHANISM PROFILING & ZERO DRY LAB UNITS: "
        "Analyze how THIS specific entity's anatomy, physical structure, environment, or human behavior actually works. "
        "Never dump raw physics/lab units (PSI, Newtons, kPa, Joules, LD50), and never insert preachy 'Most people assume...' sermons or robotic 'X is overrated/inferior' rants.",
        "   - 🎵 CONCRETE MAIN-CLAUSE MIC-DROP ENDING: End RIGHT on a concrete physical aftermath, awkward room reaction, or factual irony (e.g., 'jammed the battleship's rudder—locking 50,000 tons of steel in a helpless circle', 'pickling its body on that hidden shoreline for decades') — NEVER tack on abstract summaries ('doomed the pride of the fleet', 'Real social power belongs to...', 'That is how a...', 'That's why X belongs to...'), NEVER repeat Scene 1 concepts, and NEVER end on a subordinate 'While...' fragment.",
        "   - ⚡ THE 3 DIRECT HOT-SCRIPT SCENE-BY-SCENE RULES (LEARNED FROM 100K–4.1M VIEW YOUTUBE TRANSCRIPTS):",
        "     1. ZERO SCENE-2 DATE / ENCYCLOPEDIA THROAT-CLEARING: NEVER start Scene 2 (4.0s–9.0s) with a textbook calendar date or biography setup ('It's May 1941. The Bismarck is...', 'In 1912...', 'Back in...'). Drop Scene 2 straight into urgent physical cockpit/room/underwater action or a concrete collision analogy!",
        "     2. ZERO ADJACENT-SCENE REPETITION (SCENE 4 FOIL vs SCENE 5 MECHANISM): NEVER repeat the same physical explanation across Scene 4 and Scene 5! In Mystery/Physics scripts, use Scene 4 to show the Everyday Contrast Foil (e.g., why a sealed air-filled sub like Titan or a closed soda can implodes) and Scene 5 to show how the main subject flips that rule (e.g., water flooding every corridor of the Titanic so inside and outside pressure match).",
        "     3. CRISP 11–18 WORD SPOKEN SCENES (MAX 22 WORDS): Keep every body scene between 11 and 18 punchy spoken words so TTS delivery breathes naturally with zero rushed multi-clause sentences.",
        f"   - YAML SHOWRUNNER VOICE & PERSONA: {persona}",
    ]
    if habits:
        lines.append("   - YAML MANDATORY WRITING HABITS OF MILLION-VIEW CHANNELS:")
        for hb in habits:
            lines.append(f"     * {hb}")
    if analogy_rules:
        lines.append("   - YAML VISCERAL EVERYDAY ANALOGY TRANSLATION RULES (REPLACE DRY SCIENCE UNITS WITH THESE):")
        for ar in analogy_rules:
            lines.append(f"     * {ar}")
    if bad_ex or good_ex:
        lines.append("   - YAML GOLD-STANDARD PROSE CADENCE EXAMPLE (MATCH THIS EXACT RHYTHM):")
        if bad_ex:
            lines.append(f"     * BAD (Dry Textbook / AI Science Slop — STRICTLY FORBIDDEN): \"{bad_ex}\"")
        if good_ex:
            lines.append(f"     * GOOD (Million-View Visceral Showrunner Style — REQUIRED): \"{good_ex}\"")

    lines.append(f"   - 🔥 YAML CONTROVERSY & COMMENT-WAR BAIT ENGINE: {bait_phil}")
    if bait_examples:
        lines.append("   - YAML PROVEN COMMENT-BAIT / SACRED-COW BREAKER EXAMPLES FOR THIS NICHE:")
        for bex in bait_examples:
            lines.append(f"     * \"{bex}\"")

    return "\n" + "\n".join(lines) + "\n"


def _dedupe_str_list(items: List[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for raw in items or []:
        s = str(raw or "").strip()
        if not s:
            continue
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _compile_yaml_marker_regex(markers: List[str]) -> Optional[re.Pattern]:
    """
    Biên dịch danh sách marker từ YAML (`forbidden_edit_markers` / `forbidden_ai_slop_markers`)
    thành Regex an toàn tuyệt đối theo biên từ (`\\b`) hoặc biên hashtag (`#tag`),
    không bao giờ bắt nhầm chuỗi con (như 'ai' trong 'caiman' hay '#ai' trong '#airforce').
    """
    parts: List[str] = []
    for raw_m in _dedupe_str_list(markers):
        if raw_m.startswith("#"):
            tag_body = re.escape(raw_m[1:].strip())
            if tag_body:
                parts.append(rf"(?:^|[\s|/])#{tag_body}(?:\b|$)")
        else:
            words = [re.escape(w) for w in raw_m.split() if w]
            if words:
                sep = r"\s+"
                parts.append(rf"\b{sep.join(words)}\b")
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def get_niche_video_search_strategy(niche_id_or_profile: Any = "general") -> Dict[str, Any]:
    """
    Đọc cấu hình chiến lược tìm kiếm video Dual-Track (`llm_production_blueprint.video_search_strategy`)
    trực tiếp từ file YAML (`config/niches/*.yaml`), kế thừa mặc định từ `general.yaml` nếu ngách tùy biến thiếu khóa.
    """
    if isinstance(niche_id_or_profile, dict):
        profile = niche_id_or_profile
    else:
        profile = get_niche(str(niche_id_or_profile or "general"))

    gen_profile = get_niche("general")
    base_vss = ((gen_profile.get("llm_production_blueprint") or {}).get("video_search_strategy") or {})
    active_vss = ((profile.get("llm_production_blueprint") or {}).get("video_search_strategy") or {})

    allow_3d_cgi = bool(active_vss.get("allow_3d_cgi_video", base_vss.get("allow_3d_cgi_video", False)))
    edit_markers = _dedupe_str_list(
        list(base_vss.get("forbidden_edit_markers") or [])
        + list(active_vss.get("forbidden_edit_markers") or [])
    )
    if allow_3d_cgi:
        ai_markers = _dedupe_str_list(list(active_vss.get("forbidden_ai_slop_markers") or []))
    else:
        ai_markers = _dedupe_str_list(
            list(base_vss.get("forbidden_ai_slop_markers") or [])
            + list(active_vss.get("forbidden_ai_slop_markers") or [])
        )

    merged: Dict[str, Any] = {
        "niche_id": str(profile.get("id") or "general"),
        "dual_track_clash_rule": str(active_vss.get("dual_track_clash_rule") or base_vss.get("dual_track_clash_rule") or "").strip(),
        "single_entity_body_scene_rule": str(active_vss.get("single_entity_body_scene_rule") or base_vss.get("single_entity_body_scene_rule") or "").strip(),
        "stock_and_fallback_rule": str(active_vss.get("stock_and_fallback_rule") or base_vss.get("stock_and_fallback_rule") or "").strip(),
        "format_unlock_rule": str(active_vss.get("format_unlock_rule") or base_vss.get("format_unlock_rule") or "").strip(),
        "anti_flop_scene_continuity_rule": str(active_vss.get("anti_flop_scene_continuity_rule") or base_vss.get("anti_flop_scene_continuity_rule") or "").strip(),
        "allow_3d_cgi_video": allow_3d_cgi,
        "clash_query_examples": _dedupe_str_list(list(active_vss.get("clash_query_examples") or base_vss.get("clash_query_examples") or [])),
        "single_entity_query_examples": _dedupe_str_list(list(active_vss.get("single_entity_query_examples") or base_vss.get("single_entity_query_examples") or [])),
        "stock_fallback_examples": _dedupe_str_list(list(active_vss.get("stock_fallback_examples") or base_vss.get("stock_fallback_examples") or [])),
        "forbidden_edit_markers": edit_markers,
        "forbidden_ai_slop_markers": ai_markers,
    }
    return merged


def get_niche_forbidden_video_metadata_regexes(
    niche_id_or_profile: Any = "general",
) -> Tuple[Optional[re.Pattern], Optional[re.Pattern], bool]:
    """
    Trả về `(edit_spam_regex, ai_slop_regex, allow_3d_cgi_video)` được biên dịch có cache từ YAML `config/niches/*.yaml`.
    """
    if isinstance(niche_id_or_profile, dict):
        cache_key = str(niche_id_or_profile.get("id") or "").strip().lower()
    else:
        raw_k = str(niche_id_or_profile or "general").strip().lower()
        cache_key = NICHE_ALIASES.get(raw_k, raw_k) or "general"

    if cache_key and cache_key in _cached_video_metadata_regexes and not isinstance(niche_id_or_profile, dict):
        return _cached_video_metadata_regexes[cache_key]

    vss = get_niche_video_search_strategy(niche_id_or_profile)
    compiled = (
        _compile_yaml_marker_regex(vss.get("forbidden_edit_markers") or []),
        _compile_yaml_marker_regex(vss.get("forbidden_ai_slop_markers") or []),
        bool(vss.get("allow_3d_cgi_video", False)),
    )
    if cache_key and not isinstance(niche_id_or_profile, dict):
        _cached_video_metadata_regexes[cache_key] = compiled
    return compiled


def build_video_search_strategy_prompt_block(niche_id_or_profile: Any = "general") -> str:
    """
    Kết xuất khối chỉ dẫn `YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY` trực tiếp từ `config/niches/*.yaml`
    để tiêm đồng nhất vào ContentAgent, HookVideoNode, LLMMediaDirector và VisualCurationAgent.
    Tuyệt đối không hardcode ví dụ loài vật hay từ khóa ngách trong Python!
    """
    vss = get_niche_video_search_strategy(niche_id_or_profile)
    lines: List[str] = [
        f"🔎 YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY (FROM `config/niches/{vss['niche_id']}.yaml`):",
    ]
    if vss.get("dual_track_clash_rule"):
        lines.append(f"  * Dual-Track Clash vs. Single-Entity Rule: {vss['dual_track_clash_rule']}")
    if vss.get("single_entity_body_scene_rule"):
        lines.append(f"  * Single-Entity Body Scene Rule (NO 'A vs B' Stuffing): {vss['single_entity_body_scene_rule']}")
    if vss.get("stock_and_fallback_rule"):
        lines.append(f"  * Stock & Fallback Broad Query Rule (ZERO 'vs'): {vss['stock_and_fallback_rule']}")
    if vss.get("format_unlock_rule"):
        lines.append(f"  * Raw 16:9 + 9:16 Format Mix Rule: {vss['format_unlock_rule']}")
    if vss.get("anti_flop_scene_continuity_rule"):
        lines.append(f"  * Niche Anti-Flop Scene Continuity Guardrail: {vss['anti_flop_scene_continuity_rule']}")
    if vss.get("clash_query_examples"):
        ex_clash = ", ".join(f'"{q}"' for q in vss["clash_query_examples"][:4])
        lines.append(f"  * YAML Direct-Encounter / Hook Query Examples: {ex_clash}")
    if vss.get("single_entity_query_examples"):
        ex_single = ", ".join(f'"{q}"' for q in vss["single_entity_query_examples"][:5])
        lines.append(f"  * YAML Clean Single-Entity Real-Action Query Examples: {ex_single}")
    if vss.get("stock_fallback_examples"):
        ex_stock = ", ".join(f'"{q}"' for q in vss["stock_fallback_examples"][:4])
        lines.append(f"  * YAML Canonical Stock/Fallback Query Examples (2-4 words, NO 'vs'): {ex_stock}")
    banned_tags = _dedupe_str_list(
        list(vss.get("forbidden_edit_markers") or [])[:10]
        + list(vss.get("forbidden_ai_slop_markers") or [])[:8]
    )
    if banned_tags:
        lines.append("  * YAML Forbidden #Edit / Quiz / AI-Slop Video Markers: " + ", ".join(f'"{t}"' for t in banned_tags))
    return "\n".join(lines)


def build_script_prompt_instructions(
    niche_profile: Any,
    language: str = "en",
    video_format: str = "short",
) -> str:
    """
    Biến đổi hồ sơ ngách thành khối chỉ dẫn kịch bản chi tiết để tiêm vào Gemini ContentAgent.
    """
    if not isinstance(niche_profile, dict):
        niche_profile = get_niche(str(niche_profile or "psychology"))
    niche_name = niche_profile.get("name", "YouTube Shorts")
    badge = niche_profile.get("badge", "Channel")
    target_aud = niche_profile.get("target_audience", "Đại chúng người xem Shorts")
    blueprint = niche_profile.get("script_blueprint", {})
    is_lf = str(video_format or "short").strip().lower() in ("long", "long_form", "long-form", "16:9", "landscape", "horizontal", "documentary")

    tone = blueprint.get("tone_of_voice", "Cuốn hút, sắc bén, giàu chiều sâu thực chiến")
    pacing = blueprint.get(
        "pacing",
        "Thời lượng vàng 32 - 42 giây (8 - 9 phân cảnh trọn câu, 88 - 118 từ tổng, 10 - 14 từ/cảnh ~ 3.5 - 4.5 giây/cảnh)",
    )
    structure = blueprint.get(
        "narrative_structure",
        "Act 1: Hook 2.5s (5-8 từ) -> Act 2: Ví dụ câu nói đời thực / Bằng chứng lịch sử cụ thể (Cảnh 2-3) -> Act 3: Giải phẫu cơ chế ngầm & Mid-Video Twist (Cảnh 4-5) -> Act 4: Đòn phản công / Hóa giải thực chiến (Cảnh 6-7) -> Act 5: Cú chốt & Vòng lặp Vô tận (Cảnh 8-9, Zero Spoken Subscribe CTA)",
    )
    hooks = blueprint.get("hook_formulas", [])
    forbidden = blueprint.get("forbidden_words", [])
    banned_lex = niche_profile.get("banned_lexicon") or blueprint.get("banned_lexicon", [])
    ctas = blueprint.get("cta_templates", [])
    anti_hallucination = blueprint.get("anti_hallucination_rules", [])
    comp_bp = blueprint.get("competitor_blueprint", {}) or {}

    hooks_text = "\n".join(f"      - {h}" for h in hooks) if hooks else "      - 2.5s Pattern Interrupt Hook (5-8 words)"
    forbidden_text = "\n".join(f"      * KHÔNG ĐƯỢC DÙNG: \"{w}\"" for w in forbidden) if forbidden else "      * Tránh lời mở đầu sáo rỗng"
    banned_lex_text = "\n".join(f"      * STRICTLY FORBIDDEN VOCABULARY/CONCEPTS: \"{w}\"" for w in banned_lex) if banned_lex else ""
    ctas_text = "\n".join(f"      - {c}" for c in ctas) if ctas else "      - Câu hỏi mở kích thích tranh luận ở Pinned Comment"
    facts_text = "\n".join(f"      * {rule}" for rule in anti_hallucination) if anti_hallucination else "      * Kiểm chứng tính chính xác"

    comp_bp_block = ""
    if comp_bp:
        lf_archs = "\n".join(f"      * {a}" for a in (comp_bp.get("long_form_archetypes") or []))
        sh_archs = "\n".join(f"      * {a}" for a in (comp_bp.get("shorts_archetypes") or []))
        prog = comp_bp.get("round_or_chapter_progression", "")
        thumb_rule = comp_bp.get("thumbnail_hook_rule", "2-3 words MAX in high-contrast White + Yellow/Red")
        if is_lf:
            comp_bp_block = f"""
7. BẢN THIẾT KẾ VIRAL ĐỐI THỦ TRIỆU VIEW (COMPETITOR MILLION-VIEW BLUEPRINT — {comp_bp.get('benchmark_channel', 'Top Competitors')}):
   - Cấu trúc Long-Form 16:9 (Gauntlet / Every Record Explained / What If YOU...):
{lf_archs}
   - Tiến trình Chương / Vòng đấu (Chapter / Round Escalation): {prog}
   - Quy tắc chữ Thumbnail (`thumbnail_text`): {thumb_rule}
"""
        else:
            comp_bp_block = f"""
7. BẢN THIẾT KẾ VIRAL SHORTS 9:16 (COMPETITOR MILLION-VIEW BLUEPRINT — {comp_bp.get('benchmark_channel', 'Top Competitors')}):
   - Cấu trúc Shorts 9:16 (34–40 giây):
{sh_archs}
   - QUY TẮC KHÓA CHỦ THỂ 1V1 TRONG SHORTS (CHỐNG RỚT VIEW GIÂY 14): Trong thời lượng 34–40 giây của YouTube Shorts, TUYỆT ĐỐI CẤM chia thành nhiều vòng đấu (Round 1 -> Round 2 -> Round 3) hoặc đưa thêm con vật/chủ thể thứ 3 không có trong Tiêu đề! Toàn bộ 7–8 cảnh từ giây 0.0s đến giây cuối cùng BẮT BUỘC chỉ xoáy sâu vào đúng cặp đối đầu `Thực thể A vs Thực thể B` (hoặc hiện tượng duy nhất) đã hứa hẹn ở Tiêu đề!
   - Quy tắc chữ Thumbnail (`thumbnail_text`): {thumb_rule}
"""

    lang_low = str(language or "en").strip().lower()
    non_vi_guard = ""
    if not lang_low.startswith("vi"):
        non_vi_guard = (
            "\n⚠️ CRITICAL META-LANGUAGE WARNING: Any Vietnamese notes inside this Niche Profile are internal studio guidelines ONLY. "
            f"You MUST translate all concepts into 100% natural '{language}' and NEVER output any Vietnamese words, syllables, or diacritics in title, hook, narration, overlay_badge, or thumbnail_text!\n"
        )

    return f"""
═══════════════════════════════════════════════════════════════════════
🎯 NICHE INTELLIGENCE PROFILE: {niche_name.upper()} ({badge})
═══════════════════════════════════════════════════════════════════════{non_vi_guard}
1. KHÁN GIẢ MỤC TIÊU (TARGET AUDIENCE):
   {target_aud}

2. GIỌNG ĐIỆU ĐẶC TRƯNG, THỜI LƯỢNG VÀNG & CẤU TRÚC 5 HỒI (TONE, DEPTH & 5-ACT ARC):
   - Giọng điệu: {tone}
   - Thời lượng & Nhịp cảnh: {pacing}
   - Cấu trúc tự sự 5 hồi: {structure}
   - YÊU CẦU CHIỀU SÂU BẮT BUỘC: Tuyệt đối KHÔNG viết chung chung bề nổi! Phải có (1) Ví dụ câu nói/tình huống cụ thể ngoài đời thực hoặc dẫn chứng lịch sử/khoa học xác thực, (2) Phân tích cơ chế tại sao nó thao túng/tác động đến não bộ, và (3) Đòn phản công / Cách hóa giải cụ thể từng lời nói hoặc hành động để người xem áp dụng được ngay.

3. CÔNG THỨC HOOK 2.5 GIÂY ĐẦU DÀNH RIÊNG CHO NGÁCH NÀY (SCENE 1 HOOK BLUEPRINT):
{hooks_text}

4. BỘ TỪ KHÓA BỊ CẤM TUYỆT ĐỐI (STRICTLY FORBIDDEN CLICHÉS & OUT-OF-NICHE CONCEPTS):
{forbidden_text}
{banned_lex_text}

5. QUY TẮC KẾT VIDEO VÒNG LẶP VÔ TẬN (ZERO SPOKEN OUTRO CTA — INFINITE LOOP BRIDGE):
   - CẤM TUYỆT ĐỐI đọc câu kêu gọi "Subscribe", "Inscreva-se", "Leave a comment", "Đăng ký kênh" trong lời thoại `narration` của cảnh cuối (vì làm tụt 15% APV ở 5 giây cuối)!
   - Cảnh cuối BẮT BUỘC khép lại bằng cú chốt Payoff đắt giá và bắc cầu ý niệm nối mượt về câu Hook ở Cảnh 1.
   - Gợi ý mồi tương tác (CHỈ đặt vào trường `pinned_comment`, KHÔNG đưa vào `narration`):
{ctas_text}

6. QUY TẮC DẪN CHỨNG & XÁC THỰC (ANTI-HALLUCINATION / RESEARCH RULES):
{facts_text}{comp_bp_block}
{build_million_view_showrunner_prose_dna(niche_profile, language=language)}
9. {build_video_search_strategy_prompt_block(niche_profile)}
═══════════════════════════════════════════════════════════════════════
"""


def build_visual_strategy_prompt_instructions(niche_profile: Dict[str, Any]) -> str:
    """
    Biến đổi hồ sơ ngách thành chỉ dẫn chiến lược hình ảnh cho VisualDirectorAgent.
    """
    visual = niche_profile.get("visual_aesthetics", {})
    art_style = visual.get("art_style", "Cinematic 8k")
    angles = visual.get("camera_angles", "Dramatic angles")
    palette = visual.get("color_palette", "High-contrast cinematic")
    stock_kws = visual.get("stock_video_keywords", [])
    stock_text = ", ".join(f'"{kw}"' for kw in stock_kws[:10])
    llm_bp = build_llm_production_overview_prompt(niche_profile, include_all_niches_map=False)

    return f"""
- Phong cách mỹ thuật ngách: {art_style}
- Góc quay ưu tiên: {angles}
- Bảng màu & Ánh sáng: {palette}
- Từ khóa tư liệu thực tế (Stock keywords): {stock_text}
{llm_bp}
"""


def build_llm_production_overview_prompt(
    niche_id_or_profile: Any,
    include_all_niches_map: bool = True,
) -> str:
    """
    Trích xuất toàn cảnh Hệ sinh thái Ngách Kênh & Bản thiết kế Công cụ Sản xuất (`llm_production_blueprint`)
    trực tiếp từ các file YAML trong `config/niches/*.yaml` để cung cấp cho LLM Director / VisualCurationAgent / HookVideoNode
    tự suy luận và quyết định chọn `@tool` sản xuất video cho từng phân cảnh.
    """
    if isinstance(niche_id_or_profile, dict):
        profile = niche_id_or_profile
    else:
        profile = get_niche(str(niche_id_or_profile or "general"))

    active_id = str(profile.get("id") or "general")
    active_name = str(profile.get("name") or active_id)
    active_desc = str(profile.get("description") or "").strip()
    visual = profile.get("visual_aesthetics", {}) or {}
    bp = profile.get("llm_production_blueprint", {}) or {}

    lines: List[str] = []
    if include_all_niches_map:
        all_profiles = load_all_niches()
        core_ids = (
            "psychology",
            "wildlife_predators",
            "deep_sea_earth_mysteries",
            "alternate_history",
            "animal_rescue",
            "satirical_psychology_animation",
        )
        lines.append("═══════════════════════════════════════════════════════════════════════")
        lines.append("🌐 STUDIO-WIDE CHANNEL NICHE TAXONOMY (FROM `config/niches/*.yaml`):")
        for p in all_profiles:
            pid = str(p.get("id") or "")
            if pid in core_ids or pid == active_id:
                p_bp = p.get("llm_production_blueprint") or {}
                p_ov = str(p_bp.get("niche_overview") or p.get("description") or "").strip().replace("\n", " ")
                marker = "👉 [ACTIVE CHANNEL NICHE]" if pid == active_id else "  •"
                lines.append(f"{marker} `{pid}` ({p.get('name', pid)}): {p_ov[:220]}")

    lines.append("═══════════════════════════════════════════════════════════════════════")
    lines.append(f"🎯 ACTIVE NICHE YAML PRODUCTION BLUEPRINT: `{active_id}` ({active_name})")
    if active_desc:
        lines.append(f"- Niche Mission: {active_desc}")
    if bp.get("niche_overview"):
        lines.append(f"- Million-View Production Philosophy: {str(bp.get('niche_overview')).strip()}")
    if visual.get("art_style"):
        lines.append(f"- Visual Art Style: {visual.get('art_style')}")
    if visual.get("camera_angles"):
        lines.append(f"- Signature Camera Angles: {visual.get('camera_angles')}")
    if visual.get("stock_video_keywords"):
        kws = ", ".join(f'"{k}"' for k in list(visual.get("stock_video_keywords") or [])[:10])
        lines.append(f"- Proven Anchor Queries (YAML): {kws}")

    script_bp = profile.get("script_blueprint", {}) or {}
    comp_bp = script_bp.get("competitor_blueprint") or profile.get("competitor_blueprint") or {}
    if isinstance(comp_bp, dict) and comp_bp:
        bench_ch = str(comp_bp.get("benchmark_channel") or "").strip()
        round_prog = str(comp_bp.get("round_or_chapter_progression") or "").strip()
        thumb_rule = str(comp_bp.get("thumbnail_2_3_word_rule") or "").strip()
        lines.append("\n🏆 YAML COMPETITOR VIRAL BLUEPRINT FOR LLM DIRECTOR (`competitor_blueprint`):")
        if bench_ch:
            lines.append(f"  * Benchmark Channels: {bench_ch}")
        if round_prog:
            lines.append(f"  * Round / Chapter Progression Rule: {round_prog}")
            lines.append(
                "  * Director Scene-Level Rule: When directing a Gauntlet / Every-Record / What-If script, "
                "read each scene's narration & overlay_badge carefully — dynamically switch `subject_entity` "
                "and `video_search_query` to match that specific Round's challenger / record tier, or lock "
                "`@MODERNYOU` in `render_doodle_2d_explainer_scene` for 2nd-person 'What If YOU' chapters."
            )
        if thumb_rule:
            lines.append(f"  * Thumbnail 2-3 Word Rule: {thumb_rule}")

    gen_prose_dir = (get_niche("general").get("script_blueprint") or {}).get("showrunner_prose_dna") or {}
    prose_dna_dir = script_bp.get("showrunner_prose_dna") or gen_prose_dir or {}
    bait_dir = script_bp.get("controversy_comment_bait_engine") or {}
    if prose_dna_dir or bait_dir:
        lines.append("\n🧠 YAML SHOWRUNNER SPOKEN CREATOR VOICE, EVERYDAY IMAGERY & MYTH-FLIP ENGINE:")
        if prose_dna_dir.get("persona_and_voice"):
            lines.append(f"  * Showrunner Persona & Voice: {prose_dna_dir.get('persona_and_voice')}")
        lines.append(
            "  * Kitchen-Table Spoken Creator Voice Rule: Sound 100% like a real human YouTube storyteller (Zack D. Films / Casual Geographic / Charisma on Command / Fatos Desconhecidos). "
            "Use natural contractions ('it's', 'doesn't', 'you'd'), conversational glue ('You see,', 'But', 'So,', 'Turns out,', 'Só que', 'Por outro lado,'), and $1 everyday spoken words. "
            "STRICTLY BAN stiff AI essay syntax ('Equipped with...', 'Armed with...', 'Boasting...', 'Ostentando...'), $10 academic vocabulary ('elapid', 'permeates', 'structural differential'), "
            "dry physics units (PSI, Newtons, kPa, Joules, LD50), robotic 'X is overrated/inferior' interruptions, and verbatim copy-pasting of Scene 1 in the final scene!"
        )
        if prose_dna_dir.get("visceral_analogy_rules"):
            lines.append("  * Niche Analogy Translations (YAML): " + " | ".join(str(x) for x in list(prose_dna_dir.get("visceral_analogy_rules"))[:3]))
        if prose_dna_dir.get("good_million_view_example"):
            lines.append(f"  * Gold-Standard EN Creator Voice Benchmark: \"{prose_dna_dir.get('good_million_view_example')}\"")
        if prose_dna_dir.get("good_pt_br_example"):
            lines.append(f"  * Gold-Standard pt-BR Creator Voice Benchmark: \"{prose_dna_dir.get('good_pt_br_example')}\"")
        if bait_dir.get("philosophy"):
            lines.append(f"  * Organic Myth-Flip & Debate Philosophy: {bait_dir.get('philosophy')}")
        if bait_dir.get("bait_examples"):
            lines.append("  * Proven Organic Myth-Flip / Curiosity Angles (YAML): " + " | ".join(f'"{x}"' for x in list(bait_dir.get("bait_examples"))[:3]))

    archetypes = bp.get("visual_archetypes_and_tool_routing") or []
    if isinstance(archetypes, list) and archetypes:
        lines.append("\n🛠️ YAML VISUAL ARCHETYPES & `@tool` ROUTING MATRIX (LLM MUST DECIDE PER SCENE):")
        for arch in archetypes:
            if not isinstance(arch, dict):
                continue
            lines.append(
                f"  * {arch.get('archetype', 'Archetype')}:\n"
                f"    - When to use: {str(arch.get('when_to_use', '')).strip()}\n"
                f"    - Recommended @tool: `{arch.get('recommended_tool', '')}`\n"
                f"    - Query/Param Formula: {arch.get('query_formula', '')}"
            )

    lines.append("\n" + build_video_search_strategy_prompt_block(profile))

    translations = bp.get("abstract_to_physical_query_translations") or []
    if isinstance(translations, list) and translations:
        lines.append("\n🔄 YAML ABSTRACT-TO-PHYSICAL QUERY TRANSLATION RULES (NEVER SEARCH ABSTRACT NOUNS):")
        for tr in translations:
            lines.append(f"  * {tr}")

    forbidden_vis = bp.get("strictly_forbidden_visuals") or []
    if isinstance(forbidden_vis, list) and forbidden_vis:
        lines.append("\n🚫 STRICTLY FORBIDDEN VISUALS FOR THIS NICHE:")
        for fv in forbidden_vis:
            lines.append(f"  * {fv}")
    lines.append("═══════════════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def build_image_prompt(raw_prompt: str, scene_id: int, niche_profile: Dict[str, Any]) -> Tuple[str, str]:
    """
    Xây dựng prompt tối ưu cho ImageAgent (Prompt + Negative Prompt).
    Kết hợp visual_aesthetics của hồ sơ ngách với mô tả phân cảnh.
    """
    cleaned = re.sub(r'[\r\n\t]+', ' ', raw_prompt).strip()
    cleaned = re.sub(r'\b(watermark|logo|text|border|frame|subtitles)\b', '', cleaned, flags=re.IGNORECASE)

    visual = niche_profile.get("visual_aesthetics", {})
    prefix = visual.get("master_prompt_prefix", "8k vertical film still, 35mm photography")
    suffix = visual.get("master_prompt_suffix", "photorealistic 9:16 vertical composition, no watermark, no text")
    negative = visual.get("negative_prompts", "blurry, low quality, cartoon, watermark, text")

    # Chủ thể cảnh đứng ĐẦU prompt: FLUX/Gemini ưu tiên token xuất hiện sớm, prefix phong cách dài sẽ làm "loãng" chủ thể.
    if scene_id == 1:
        # Cảnh Hook mở màn: Tăng cường độ thị giác với màu sắc sống động
        full_prompt = f"{cleaned}. Style: viral dramatic cinematic hook, {prefix}, {suffix}, extreme visual impact, razor-sharp focus, rich color depth"
    else:
        full_prompt = f"{cleaned}. Style: {prefix}, {suffix}"

    return full_prompt, negative


def hex_to_ass_color(hex_str: str, alpha: int = 0) -> str:
    """
    Chuyển đổi màu HEX (#RRGGBB) sang định dạng màu ASS (&HAABBGGRR&).
    Ví dụ: #FFE600 (Yellow) -> &H0000E6FF&
    """
    clean_hex = hex_str.strip().lstrip("#")
    if len(clean_hex) == 3:
        clean_hex = "".join(c * 2 for c in clean_hex)
    if len(clean_hex) != 6:
        clean_hex = "FFFFFF"

    r = int(clean_hex[0:2], 16)
    g = int(clean_hex[2:4], 16)
    b = int(clean_hex[4:6], 16)
    a = max(0, min(255, alpha))

    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}&"


def get_niche_subtitle_style(niche_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trả về bộ mã màu và font chữ đã chuyển đổi cho ASS Subtitles từ hồ sơ ngách.
    Chuẩn hóa Safe Zone YouTube Shorts trên khung 1080x1920:
      - MarginV: 380 - 480px (mặc định 420px: nằm gọn dưới chủ thể trung tâm và hoàn toàn phía trên vùng tiêu đề/@handle của YouTube Shorts).
      - Fontsize: 52 - 62px (mặc định 56px: thanh thoát, rõ nét, không choán khung hình).
    """
    sub_cfg = niche_profile.get("subtitles", {})
    primary_hex = sub_cfg.get("primary_color", "#E4E4E4")
    highlight_hex = sub_cfg.get("highlight_color", "#FFFFFF")
    outline_hex = sub_cfg.get("outline_color", "#000000")
    raw_font = sub_cfg.get("font_family") or "Montserrat ExtraBold, sans-serif"
    font_family = raw_font.split(",")[0].strip() or "Montserrat ExtraBold"
    raw_fs = int(sub_cfg.get("font_size", 56))
    font_size = raw_fs if 52 <= raw_fs <= 62 else 56

    raw_margin = int(sub_cfg.get("margin_bottom", 420))
    margin_v = raw_margin if 380 <= raw_margin <= 480 else 420

    return {
        "fontname": font_family,
        "fontsize": font_size,
        "margin_v": margin_v,
        "primary_ass": hex_to_ass_color(primary_hex, alpha=0),
        "secondary_ass": hex_to_ass_color(highlight_hex, alpha=0),
        "outline_ass": hex_to_ass_color(outline_hex, alpha=0),
        "primary_hex": primary_hex,
        "highlight_hex": highlight_hex,
        "outline_hex": outline_hex,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HARD NICHE FIREWALL (CHỐNG NHIỄM CHÉO NGÁCH TUYỆT ĐỐI)
# ─────────────────────────────────────────────────────────────────────────────

NICHE_EXCLUSIVE_SIGNATURES: Dict[str, Tuple[List[str], str]] = {
    "deep_sea_earth_mysteries": (
        [
            "kraken", "leviathan", "megalodon", "mariana trench", "bermuda triangle",
            "deep ocean", "ocean floor", "sea monster", "sonar recorded", "hydrophone",
            "antarctic ice", "subterranean cave", "giant squid", "colossal squid",
            "ocean deadliest", "ocean's deadliest", "abyssal", "quái vật biển", "đáy đại dương",
            "fossa das marianas", "fundo do oceano", "fundo do mar", "triângulo das bermudas",
            "oceano escondido", "ponto nemo", "buraco negro que engole navios",
            "tubarão-branco", "cachalote", "lula-colossal", "lula gigante", "abismo marinho",
            "zona hadal", "submarino", "oceano pacífico", "oceano atlântico", "great white shark",
            "sperm whale", "greenland shark", "tubarão-da-groenlândia",
        ],
        "ch_the_mysteries_of_the_world",
    ),
    "wildlife_predators": (
        [
            "jaguar vs", "caiman vs", "anaconda vs", "honey badger", "komodo dragon",
            "apex predator", "bite force", "harpy eagle", "giant river otter",
            "crocodile attack", "snow leopard", "secretary bird", "black mamba",
            "onça-pintada", "onça vs", "sucuri vs", "sucuri de", "jacaré-açu", "ariranha",
            "harpia", "lobo-guará", "tatu-canastra", "texugo-do-mel", "dragão-de-komodo",
            "pantanal", "savana",
        ],
        "ch_wild_odyssey",
    ),
    "animal_rescue": (
        [
            "stray puppy", "stray kitten", "abandoned puppy", "abandoned dog",
            "rescue dog", "rescuer", "shelter dog", "matted fur", "adopted dog",
        ],
        "ch_paws_hearts_rescue",
    ),
    "psychology": (
        [
            "psychopath", "sociopath", "dark psychology", "forensic psychology",
            "body language", "micro-expression", "pathological liar", "fbi profiler",
            "interrogation", "polygraph", "serial killer", "courtroom", "human brain",
            "brain experiment", "sensory deprivation", "stanford prison", "dopamine",
            "casino psychology", "cognitive bias", "lie detector", "decibel chamber",
        ],
        "ch_psychology_of_the_world",
    ),
    "alternate_history": (
        [
            "roman empire never", "library of alexandria", "mongol empire",
            "alternate history", "what if history", "archduke franz ferdinand",
        ],
        "@alternatehistorywhatif-e2x",
    ),
}


def validate_channel_niche_alignment(
    channel_id: str,
    title: str = "",
    topic: str = "",
    script: str = "",
) -> Dict[str, Any]:
    """
    Tường lửa Ngách Cứng (Hard Niche Firewall):
    Kiểm tra xem tiêu đề/chủ đề/kịch bản có thuộc đúng ngách của `channel_id` hay không.
    - Phát hiện vi phạm `banned_lexicon` của ngách.
    - Phát hiện hiện tượng gán nhầm video của ngách khác (ví dụ bài Đại dương gán nhầm vào kênh Tâm lý).
    Trả về:
      {
        "passed": bool,
        "channel_niche": str,
        "detected_niche": Optional[str],
        "suggested_channel_id": Optional[str],
        "reason": str
      }
    """
    profile = get_channel_niche(channel_id)
    channel_niche = str(profile.get("id") or "general").strip().lower()
    if channel_niche == "general":
        return {
            "passed": True,
            "channel_niche": channel_niche,
            "detected_niche": None,
            "suggested_channel_id": None,
            "reason": "General channel allows all niches.",
        }

    combined_head = f"{title or ''} {topic or ''}".lower()
    combined_full = f"{combined_head} {(script or '')[:600]}".lower()

    # 1. Kiểm tra banned_lexicon trong tiêu đề và chủ đề
    banned_list = profile.get("banned_lexicon") or profile.get("script_blueprint", {}).get("banned_lexicon", [])
    for banned_term in banned_list:
        bt = str(banned_term).strip().lower()
        if bt and len(bt) >= 3 and re.search(rf"(?<![\w]){re.escape(bt)}(?![\w])", combined_head):
            return {
                "passed": False,
                "channel_niche": channel_niche,
                "detected_niche": "banned_lexicon",
                "suggested_channel_id": None,
                "reason": f"Tiêu đề/Chủ đề chứa từ khóa bị cấm tuyệt đối ('{bt}') đối với ngách '{channel_niche}'.",
            }

    # 1.5. Tường lửa cứng tách biệt tuyệt đối Kênh Trên Cạn (`wildlife_predators` / `ch_odisseia_selvagem`)
    # và Kênh Về Biển (`deep_sea_earth_mysteries` / `ch_misterios_do_mundo`) để không bao giờ đăng nhầm
    cid_low = str(channel_id or "").lower()
    is_br = cid_low in (
        "ch_odisseia_selvagem", "ch_misterios_do_mundo",
        "@odisseiaselvagemoficial", "@misteriosdomundo-tv",
        "ucpbqs4h5qugpu3hcqjg-p9w", "ucvfonq9jcpvvze8e1cgnng",
    )
    if channel_niche == "wildlife_predators":
        marine_only_terms = (
            "fossa das marianas", "ponto nemo", "lula-colossal", "lula gigante", "cachalote",
            "abismo marinho", "zona hadal", "fundo do oceano", "fundo do mar", "submarino",
            "tubarão-da-groenlândia", "tubarão-branco", "great white shark", "mariana trench",
            "colossal squid", "sperm whale", "deep ocean", "ocean floor", "seafloor", "kraken", "leviathan",
        )
        hit_marine = [m for m in marine_only_terms if re.search(rf"(?<![\w]){re.escape(m)}(?![\w])", combined_head)]
        if hit_marine:
            return {
                "passed": False,
                "channel_niche": channel_niche,
                "detected_niche": "deep_sea_earth_mysteries",
                "suggested_channel_id": "ch_misterios_do_mundo" if is_br else "ch_the_mysteries_of_the_world",
                "reason": (
                    f"Kênh Trên Cạn ('{channel_id}') không được đăng nội dung Biển Sâu/Đại Dương "
                    f"({', '.join(hit_marine[:2])})."
                ),
            }
    elif channel_niche == "deep_sea_earth_mysteries":
        land_only_terms = (
            "onça-pintada", "onça vs", "sucuri", "jacaré-açu", "ariranha", "harpia",
            "texugo-do-mel", "dragão-de-komodo", "mamba-negra", "pantanal", "savana",
            "leopardo", "hipopótamo", "leão", "tigre", "elefante", "jaguar vs", "caiman vs",
            "honey badger", "harpy eagle", "komodo dragon", "snow leopard", "black mamba",
        )
        hit_land = [l for l in land_only_terms if re.search(rf"(?<![\w]){re.escape(l)}(?![\w])", combined_head)]
        if hit_land:
            return {
                "passed": False,
                "channel_niche": channel_niche,
                "detected_niche": "wildlife_predators",
                "suggested_channel_id": "ch_odisseia_selvagem" if is_br else "ch_wild_odyssey",
                "reason": (
                    f"Kênh Về Biển ('{channel_id}') không được đăng nội dung Động Vật Trên Cạn "
                    f"({', '.join(hit_land[:2])})."
                ),
            }

    # 2. Kiểm tra xem tiêu đề/chủ đề có mang dấu hiệu đặc trưng độc quyền của ngách khác không
    generic_channel_tags = {
        "shorts", "ytshorts", "us shorts", "usa", "united states", "american",
        "brasil", "curiosidades", "curiosidades do mundo", "fatos desconhecidos",
        "ciência", "science", "facts", "documentary",
    }
    own_keywords_set = set(NICHE_EXCLUSIVE_SIGNATURES.get(channel_niche, ([], ""))[0])
    try:
        from config.settings import settings
        ch_cfg = settings.get_channel_by_id(channel_id) or {}
        for ckw in ch_cfg.get("channel_keywords", []) or []:
            ckw_clean = str(ckw or "").strip().lower()
            if len(ckw_clean) >= 4 and ckw_clean not in generic_channel_tags:
                own_keywords_set.add(ckw_clean)
    except Exception:
        pass

    has_own_signature = any(
        re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", combined_full)
        for kw in own_keywords_set
    )

    if not has_own_signature:
        for other_niche, (sig_keywords, suggested_ch) in NICHE_EXCLUSIVE_SIGNATURES.items():
            if other_niche == channel_niche:
                continue
            matched_foreign = [
                kw for kw in sig_keywords
                if re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", combined_head)
            ]
            if matched_foreign:
                # Nếu đang ở kênh Brazil (pt-BR), gợi ý sang kênh Brazil tương ứng
                cid_low = str(channel_id or "").lower()
                is_br = cid_low in ("ch_odisseia_selvagem", "ch_misterios_do_mundo", "@odisseiaselvagemoficial", "@misteriosdomundo-tv")
                if is_br:
                    if other_niche == "wildlife_predators":
                        suggested_ch = "ch_odisseia_selvagem"
                    elif other_niche == "deep_sea_earth_mysteries":
                        suggested_ch = "ch_misterios_do_mundo"
                return {
                    "passed": False,
                    "channel_niche": channel_niche,
                    "detected_niche": other_niche,
                    "suggested_channel_id": suggested_ch,
                    "reason": (
                        f"Phát hiện lệch ngách (Cross-Niche Contamination): Nội dung chứa dấu hiệu của ngách "
                        f"'{other_niche}' ({', '.join(matched_foreign[:2])}) nhưng đang định đăng lên kênh ngách '{channel_niche}'."
                    ),
                }

    return {
        "passed": True,
        "channel_niche": channel_niche,
        "detected_niche": channel_niche,
        "suggested_channel_id": channel_id,
        "reason": "OK",
    }

