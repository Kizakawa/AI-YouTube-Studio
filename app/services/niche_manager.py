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
    global _cached_niches
    if _cached_niches is not None and not force_reload:
        return list(_cached_niches.values())

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

def build_million_view_showrunner_prose_dna(niche_id: str, language: str = "en") -> str:
    """
    Hệ thống Khuôn Mẫu Văn Phong & Nhịp Câu Của Các Kênh Triệu View (Million-View Showrunner Prose DNA):
    Ép LLM từ bỏ hoàn toàn lối viết văn mẫu AI học thuật (AI Slop) và nhập vai 100% vào giọng văn,
    cách ngắt nhịp câu, cách tung thông số và trích dẫn hội thoại của các kênh lớn nhất thế giới ở từng ngách.
    """
    nid = NICHE_ALIASES.get(str(niche_id or "general").strip().lower(), str(niche_id or "general").strip().lower())
    is_pt = str(language or "en").strip().lower().startswith("pt")

    anti_ai_slop_block = (
        "🚫 UNIVERSAL ANTI-AI PROSE BAN (NEVER SOUND LIKE CHATGPT / TEXTBOOK AI):\n"
        "   - STRICTLY BANNED AI FILLER PHRASES: NEVER write 'When we examine...', 'In forensic behavioral analysis...', "
        "'Researchers discovered that...', 'Furthermore,', 'Moreover,', 'It is important to note', 'Delve into', 'Tapestry', "
        "'Ultimately,', 'Unleash', 'Understanding this mechanism allows you to...', or 'In conclusion'.\n"
        "   - SENTENCE CADENCE RULE: Write in punchy, spoken Grade-5 broadcast sentences. Alternate a crisp 6–9 word punch sentence "
        "with a vivid 12–16 word action/mechanism sentence. Every single sentence must either (a) show a concrete physical action/quote, "
        "(b) drop a hard number/metric, or (c) flip the viewer's expectation ('But here is the trap...', 'Yet three seconds later...')."
    )

    if "psychology" in nid:
        return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: JCS Criminal Psychology × Charisma on Command × Chase Hughes × @IFinity_What):
   {anti_ai_slop_block}
   - VOICE & PERSONA: Cold, surgical CIA/FBI Behavioral Profiler & Social Dynamics Breakdown Artist.
   - 3 MANDATORY WRITING HABITS OF MILLION-VIEW PSYCHOLOGY CHANNELS:
     1. **Open With a Micro-Expression or Verbatim Spoken Trap ("Show, Don't Tell")**: Never lecture abstractly about "manipulation". Quote the exact words a manipulator says in real life (e.g., '"You are way too sensitive" is not an opinion—it is a calculated bait trap.') or point to an involuntary body reflex ('Watch a suspect's blink rate the exact second you stop nodding.').
     2. **Name the Real Forensic / Neural Protocol**: Cite specific, real-world profiling concepts (*CIA Elicitation*, *The 4-Second Silence Audit*, *Suprasternum Notch Pacifying Touch*, *0.2-Second Amygdala Hijack*, *Intermittent Dopamine Reinforcement*) backed by concrete time/percentage numbers.
     3. **Deliver a Word-for-Word Verbal Counter-Move**: Always give the viewer the exact spoken sentence in quotation marks to disarm the trap on the spot (e.g., 'Hold eye contact for four full seconds, lower your voice, and ask: "What did you hope I would feel when you said that?"').
   - GOLD-STANDARD PROSE CADENCE EXAMPLE (MATCH THIS EXACT RHYTHM):
     * BAD (Generic AI Slop): "When someone uses silence to manipulate you, it triggers anxiety in your brain. In forensic behavioral analysis, staying calm exposes their leverage."
     * GOOD (JCS / Chase Hughes Style): "CIA interrogators never argue when a suspect lies—they just go dead silent for four seconds. Without your voice to push against, the liar's amygdala panics and forces them to talk."
"""
    elif "wildlife" in nid:
        if is_pt:
            return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: TierZoo × Casual Geographic × @FossilVideos × BBC Earth Brasil):
   {anti_ai_slop_block}
   - VOZ E ESTILO: Analista de Biomecânica de Combate Predatório (ritmo intenso, direto, estilo TierZoo + documentário de elite).
   - 3 HÁBITOS DE ESCRITA DOS CANAIS DE MILHÕES DE VIEWS:
     1. **Análise de Armas Biológicas e Estatísticas de Combate**: Trate cada confronto como um duelo tático pesado — cite peso exato, força de mordida em PSI, velocidade de reação em milissegundos e anatomia letal.
     2. **Zero Poesia Lenta**: Nunca escreva descrições genéricas como "A natureza é fascinante". Cada frase deve mostrar um ataque físico, defesa de armadura ou contra-ataque mortal.
     3. **Reviravolta Biomecânica na Metade (2-Way Duel)**: Mostre por que o predador parece invencível no início, mas revele a arma secreta do oponente na Cena 4.
   - EXEMPLO DE RITMO DE OURO (IMITAR ESTE ESTILO):
     * RUIM (IA Genérica): "A onça-pintada é um predador muito forte que vive no Pantanal e caça jacarés com sua mordida poderosa."
     * BOM (TierZoo / @FossilVideos Style): "Enquanto os leões levam dez minutos para sufocar a garganta, a onça-pintada ignora o pescoço. Ela crava mil e quinhentos PSI direto no crânio do jacaré, desligando o cérebro em menos de um segundo."
"""
        return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: TierZoo × Casual Geographic × @FossilVideos × BBC Earth):
   {anti_ai_slop_block}
   - VOICE & PERSONA: High-Adrenaline Combat Biomechanics Analyst (TierZoo stats breakdown meets @FossilVideos Gauntlet intensity).
   - 3 MANDATORY WRITING HABITS OF MILLION-VIEW WILDLIFE CHANNELS:
     1. **Combat Stats & Anatomical Weapon Breakdown**: Treat every animal encounter like a heavyweight engineering duel—cite exact weight (lbs/kg), bite force (PSI), strike speed (milliseconds / mph), venom lethality, and armor thickness.
     2. **Zero Slow Nature Poetry**: Never write textbook filler like "Nature is full of amazing predators." Every sentence must describe a physical strike, biomechanical advantage, or survival counter-mechanism.
     3. **Two-Way Counter-Build Twist**: Establish Predator A's lethal weapon in Act 1–2, then flip the fight in Act 3 by revealing Opponent B's biological counter-weapon!
   - GOLD-STANDARD PROSE CADENCE EXAMPLE (MATCH THIS EXACT RHYTHM):
     * BAD (Generic AI Slop): "The jaguar is a powerful apex predator that uses its strong jaws to hunt caimans in the river."
     * GOOD (TierZoo / @FossilVideos Style): "Lions spend ten minutes suffocating a prey's windpipe. The Pantanal Jaguar skips the throat entirely—driving 1,500 PSI straight through the caiman's skull to shut down its brainstem in 0.3 seconds."
"""
    elif "deep_sea" in nid or "mysteries" in nid:
        if is_pt:
            return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: Fern × Lemmino × @disastersexplained1 × Você Sabia?):
   {anti_ai_slop_block}
   - VOZ E ESTILO: Documentarista Forense de Mistérios Abissais e Anomalias Extremas da Terra.
   - 3 HÁBITOS DE ESCRITA DOS CANAIS DE MILHÕES DE VIEWS:
     1. **Imersão Sensorial em 2ª Pessoa + Registro de Profundidade/Temperatura**: Coloque o espectador dentro do fenômeno com números exatos (metros de profundidade, graus Celsius, pressão em PSI, decibéis).
     2. **O Erro Fatal do Observador**: Comece mostrando o sinal visual enganoso que parece inofensivo antes do desastre acontecer.
   - EXEMPLO DE RITMO DE OURO:
     * BOM (@disastersexplained1 / Fern Style): "A trezentos metros sob o México, o ar dentro da Caverna de Naica atinge cinquenta e oito graus com noventa e nove por cento de umidade. A cada respiração, o vapor começa a condensar dentro dos seus próprios pulmões."
"""
        return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: Fern × Lemmino × @disastersexplained1 × Zack D. Films):
   {anti_ai_slop_block}
   - VOICE & PERSONA: Forensic Expedition Chronicler & Extreme Physics Investigator (Fern / @disastersexplained1 precision).
   - 3 MANDATORY WRITING HABITS OF MILLION-VIEW MYSTERY CHANNELS:
     1. **2nd-Person Sensory Danger + Escalating Physical Telemetry**: Drop the viewer directly into the anomaly using exact depth (meters/feet), pressure (PSI), temperature (°C), or acoustic decibels (dB).
     2. **The Deceptive Calm Hook**: Show the eerie, seemingly harmless visual sign that precedes the catastrophe ('You notice the waves forming a neat square grid near the beach. Stopping to film it is a fatal mistake.').
     3. **Hard Scientific Mechanism Over Vague Spookiness**: Explain the exact physical/biological law causing the anomaly (e.g., methane hydrate blowouts, halocline density traps, 230 dB sperm whale sonar cavitation).
   - GOLD-STANDARD PROSE CADENCE EXAMPLE (MATCH THIS EXACT RHYTHM):
     * BAD (Generic AI Slop): "The deep ocean is full of scary mysteries and extreme pressure that humans cannot survive."
     * GOOD (Fern / @disastersexplained1 Style): "At two thousand meters deep, sunlight vanishes completely and water pressure crushes steel at three thousand pounds per square inch. Yet sonar just tracked a fourteen-meter colossal squid rising straight toward the hull."
"""
    elif "alternate_history" in nid or "history" in nid:
        return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: AlternateHistoryHub × RealLifeLore × Fern × @IFinity_What):
   {anti_ai_slop_block}
   - VOICE & PERSONA: High-Stakes Tick-Tock Historical Thriller & Counterfactual Domino Architect.
   - 3 MANDATORY WRITING HABITS OF MILLION-VIEW HISTORY CHANNELS:
     1. **In-Medias-Res Split-Second Opening**: Never open with a slow biography. Start at the exact minute, location, and physical switch where history almost broke ('On January 24th, 1961, two four-megaton hydrogen bombs fell over North Carolina—and five of the six safety switches failed on impact.').
     2. **Chronological Domino Escalation (Minute 1 -> Hour 24 -> Day 7 -> Year 1)**: Trace the exact chain reaction of military, geographical, and human consequences with hard numbers (megatons, miles, casualty counts, temperatures).
"""
    elif "rescue" in nid or "animal" in nid:
        return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA (BENCHMARK: The Dodo × GeoBeats Animals × Rocky Kanaka):
   {anti_ai_slop_block}
   - VOICE & PERSONA: Warm, Empathetic Micro-Moment Storyteller (GeoBeats Animals 10.8% Like-Rate style).
   - 3 MANDATORY WRITING HABITS OF MILLION-VIEW RESCUE CHANNELS:
     1. **Anchor on One Tiny, Heart-Melting Physical Detail**: Instead of generic sadness, describe the exact small object or gesture ('For four rainy nights behind the train station, this tiny puppy rested his chin on an abandoned boot just to stay off the cold concrete.').
     2. **Time-Stamped Trust Breakthrough (Day 1 -> Minute 50 -> Day 30)**: Show the exact second the animal's fear melts into trust—resting their head on a rescuer's palm, wagging their tail in a warm towel, and sprinting across a sunlit yard by Day 30.
"""
    return f"""
8. MILLION-VIEW SHOWRUNNER WRITING STYLE DNA:
   {anti_ai_slop_block}
"""


def build_script_prompt_instructions(
    niche_profile: Dict[str, Any],
    language: str = "en",
    video_format: str = "short",
) -> str:
    """
    Biến đổi hồ sơ ngách thành khối chỉ dẫn kịch bản chi tiết để tiêm vào Gemini ContentAgent.
    """
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
{build_million_view_showrunner_prose_dna(niche_profile.get("id", "general"), language=language)}
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
            "tubarão", "baleia", "cachalote", "lula-colossal", "lula gigante", "abismo marinho",
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
    own_keywords, _ = NICHE_EXCLUSIVE_SIGNATURES.get(channel_niche, ([], ""))
    has_own_signature = any(
        re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", combined_full)
        for kw in own_keywords
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

