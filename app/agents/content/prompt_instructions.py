"""app.agents.content.prompt_instructions — Chỉ dẫn toàn vẹn ngôn ngữ & công thức Hook triệu view theo thị trường."""
import json
import logging
import requests
import time
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
from app.utils.langsmith_tracer import trace_custom_span
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)



def build_language_integrity_instruction(language: str) -> str:
    lang_clean = (language or "en").strip().lower()
    is_vi = lang_clean.startswith("vi")
    is_pt = lang_clean.startswith("pt")
    if is_vi:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% TIẾNG VIỆT (VIETNAMESE)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: VIETNAMESE ('vi').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, dramatic, conversational VIETNAMESE.
  * Even if the topic, director notes, or instructions are in English, you MUST TRANSLATE and write all narration in VIETNAMESE.
  * STRICTLY FORBIDDEN: Do NOT output English spoken narration.
- TITLE REQUIREMENT:
  * 'title' MUST be in VIETNAMESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- VN', '- VI', '(Việt Nam)'.
- NEVER mix languages (e.g. Vietnamese narration with English title or vice-versa).
═══════════════════════════════════════════════════════════════════
"""
    elif is_pt:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% PORTUGUÊS BRASILEIRO (pt-BR)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: BRAZILIAN PORTUGUESE ('pt-BR').
- SPOKEN NARRATION & ON-SCREEN HUD REQUIREMENT:
  * 'hook', 'full_narration', 'narration', 'overlay_badge', 'thumbnail_text', 'description', and 'pinned_comment' in EVERY scene MUST be written 100% in natural, dramatic, colloquial BRAZILIAN PORTUGUESE (português do Brasil).
  * Even if the topic, director notes, or niche instructions are in English, you MUST TRANSLATE and write ALL narration and badges in BRAZILIAN PORTUGUESE.
  * Use vocabulary and expressions natural to Brazil: 'onça-pintada', 'sucuri', 'jacaré', 'incrível', 'absurdo', 'impressionante', 'jamais', NOT European Portuguese.
  * STRICTLY FORBIDDEN: Do NOT output English or European Portuguese spoken narration.
  * ZERO SPOKEN OUTRO CTA: NEVER say 'Inscreva-se', 'Curta', or 'Deixe nos comentários' inside any scene's spoken `narration`! Put engagement questions ONLY in `pinned_comment`, and end the final scene's `narration` with a seamless Infinite Loop Bridge back to Scene 1!
- VISUAL SEARCH & AI PROMPT REQUIREMENT (CRITICAL FOR PEXELS / PIXABAY / FLUX):
  * 'visual_keyword', 'visual_prompt', and 'stock_query' MUST ALWAYS BE IN CONCRETE ENGLISH (e.g., "jaguar hunting caiman river", "deep ocean sonar submarine", "giant river otter amazon") so stock video APIs (Pexels/Pixabay) and AI image generators return 100% accurate visuals!
- TITLE REQUIREMENT:
  * 'title' MUST be 100% in BRAZILIAN PORTUGUESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- PT', '- BR', '(Brasil)'.
- thumbnail_text MUST also be in PORTUGUÊS BRASILEIRO (3-5 words ALL CAPS + 1 emoji).
═══════════════════════════════════════════════════════════════════
"""
    else:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% ENGLISH (AMERICAN)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: ENGLISH ('en').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, punchy, conversational AMERICAN ENGLISH.
  * Even if the topic, director notes, or niche instructions are in Vietnamese, you MUST TRANSLATE and write all narration in ENGLISH.
  * STRICTLY FORBIDDEN: Do NOT output Vietnamese spoken narration.
- TITLE REQUIREMENT:
  * 'title' MUST be in ENGLISH (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- EN', '- US', '(English)'.
- NEVER mix languages (e.g. English narration with Vietnamese title or vice-versa).
═══════════════════════════════════════════════════════════════════
"""


def build_regional_hook_mastery_instruction(language: str, channel_niche: str = "general") -> str:
    """
    Xây dựng chỉ dẫn tối ưu Hook 0.0s–2.0s chuyên biệt theo từng khu vực địa lý (US/Global English vs. Brasil pt-BR vs. Việt Nam)
    dựa trên dữ liệu phân tích 343 video YouTube Shorts triệu view (1M–371M views) từ YouTube Data API v3.
    """
    lang_clean = (language or "en").strip().lower()
    niche_clean = (channel_niche or "general").strip().lower()
    is_pt = lang_clean.startswith("pt")
    is_vi = lang_clean.startswith("vi")

    bench_lines: List[str] = []
    try:
        from pathlib import Path
        bench_path = Path(settings.BASE_DIR) / "config" / "million_view_benchmarks.json"
        if bench_path.exists():
            bench_data = json.loads(bench_path.read_text(encoding="utf-8"))
            niche_map = bench_data.get("niches", {})
            matched_niche = niche_map.get(niche_clean)
            if not matched_niche:
                for nk, nv in niche_map.items():
                    if nk in niche_clean or niche_clean in nk:
                        matched_niche = nv
                        break
            if matched_niche:
                ex_list = matched_niche.get("top_million_view_examples", [])[:4]
                for ex in ex_list:
                    bench_lines.append(
                        f'  * [{ex.get("views", 0):,} views | {ex.get("duration_sec")}s | 👍{ex.get("like_rate_pct")}%] '
                        f'"{ex.get("title")}" ({ex.get("channel")})'
                    )
                bp_rule = matched_niche.get("winning_hook_blueprint", "")
                if bp_rule:
                    bench_lines.append(f"  * WINNING 0.0s HOOK BLUEPRINT: {bp_rule}")
    except Exception:
        pass

    bench_block = (
        "\n📊 REAL MILLION-VIEW YOUTUBE SHORTS DATA FOR THIS NICHE:\n" + "\n".join(bench_lines)
        if bench_lines else ""
    )

    if is_pt:
        return f"""
═══════════════════════════════════════════════════════════════════
🇧🇷 REGIONAL HOOK MASTERY — MERCADO BRASILEIRO (pt-BR | META: STAYED-TO-WATCH >= 70% & APV >= 100%)
═══════════════════════════════════════════════════════════════════
1. REGRA DOS 1,8 SEGUNDOS NO BRASIL (CENA 1 = 5 A 8 PALAVRAS NO MÁXIMO):
   - No feed de Shorts do Brasil, 85% do público decide deslizar nos primeiros 1,8s.
   - A Cena 1 DEVE ter entre 5 e 8 palavras (1,6s a 2,2s), falada com ritmo urgente, coloquial e hiper-visual!
2. 3 FÓRMULAS DE HOOK COMPROVADAS EM SHORTS DE +10 MILHÕES DE VIEWS NO BRASIL:
   - [Fórmula A — Apontamento Visual + Anomalia Mortal]: Comece apontando diretamente para o que está na tela no frame 0.0s + consequência física imediata ("Esse lago no fundo do mar paralisa tubarões!", "A 93 graus, esse rio da Amazônia ferve tudo!").
   - [Fórmula B — Duelo Brutal A vs B + Número Físico (PSI / Volts / Metros)]: Nomeie os dois oponentes + a arma biológica exata logo nos primeiros 2 segundos ("1.500 PSI: a onça perfura o crânio do jacaré!", "Nem o jacaré-açu encara um bando de ariranhas!").
   - [Fórmula C — Escala Absurda / Segredo Proibido]: Revele um número chocante imediatamente ("260 naves espaciais afundaram nesse ponto secreto do oceano!").
3. PROIBIDO NO MERCADO BRASILEIRO:
   - NUNCA comece com frases lentas de documentário de TV ou metáforas abstratas.
   - NUNCA comece com rótulo de dicionário sem verbo ativo ("Nome-do-Animal: um soco a 80 km/h", "Harpia: garras de 530 PSI"). Comece SEMPRE com ação imediata + paradoxo ("Essas garras esmagam ossos em segundos!", "Seis leões famintos recuam deste animal!").
   - NUNCA faça perguntas passivas ("Você sabia que...?").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    elif is_vi:
        return f"""
═══════════════════════════════════════════════════════════════════
🇻🇳 REGIONAL HOOK MASTERY — THỊ TRƯỜNG VIỆT NAM (vi-VN | MỤC TIÊU: STAYED-TO-WATCH >= 70%)
═══════════════════════════════════════════════════════════════════
1. QUY TẮC 1.8 GIÂY ĐẦU (CẢNH 1 = 6 ĐẾN 9 TỪ):
   - Mở đầu trực diện vào tình huống đời thực, con số gây sốc hoặc đòn tâm lý sắc lẹm ngay ở giây 0.0s.
   - Tuyệt đối không mở đầu bằng câu hỏi thụ động ("Bạn có biết...") hay văn phong sách giáo khoa.{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    else:
        return f"""
═══════════════════════════════════════════════════════════════════
🇺🇸 REGIONAL HOOK MASTERY — US / GLOBAL ENGLISH MARKET (en-US | TARGET: STAYED-TO-WATCH >= 70%)
═══════════════════════════════════════════════════════════════════
1. THE 1.8-SECOND US FEED SURVIVAL RULE (SCENE 1 = 5 TO 8 WORDS MAX | GRADE-5 CONVERSATIONAL ENGLISH):
   - Telemetry across our US channels shows that poetic/literary metaphors ("That sweet compliment is a loaded weapon", "A word. A face. A sound.") cause 71%–79% Swipe-Away because US viewers swipe before decoding abstract language!
   - Million-view US Shorts (Zack D. Films 51M, Factasm 92M, Darkful Mind 16M, Smart Nonsense 26M) use ULTRA-SIMPLE, GRADE-5 CONVERSATIONAL AMERICAN ENGLISH that points directly at the visual subject or a real-life situation in 5 to 8 words (1.6s–2.2s):
2. NICHE-SPECIFIC MILLION-VIEW US HOOK ARCHETYPES (CHOOSE THE EXACT FIT):
   - [Dark Psychology & Human Behavior]: Open with a concrete everyday quote/insult or a 2-second behavioral test:
     * "When someone says 'just joking,' watch their eyes."
     * "Turn any insult into instant respect with three words."
     * "Three body language signs someone is lying right now."
   - [Deep Sea & Earth Mysteries]: Open with a visual deictic pointer ("Look at this...", "At 2,000 meters...") + immediate physical danger:
     * "Look at this diver's suit at 1,000 feet."
     * "Scientists sealed this toxic cave for five million years."
     * "Every second counts when this deep-sea suit depressurizes."
   - [Wildlife Apex Predators]: Open with real-time play-by-play confrontation + biomechanical number:
     * "This jaguar crushes caiman skulls with 1,500 PSI."
     * "Six adult lions backed down from one honey badger."
   - [Alternate History & What If]: Open with the exact year/person/object that almost erased modern life:
     * "In 1962, this one man stopped World War Three."
     * "What if Rome industrialized steam power 1,800 years early?"
   - [Animal Rescue]: Open at the exact emotional turning point on screen:
     * "This terrified stray puppy refused to let go."
     * "Nobody recognized this stray dog thirty days later."
3. STRICTLY BANNED IN US HOOKS:
   - NO abstract poetry, NO cryptic 2-word fragments, NO slow 13+ word setup sentences in Scene 1!{bench_block}
═══════════════════════════════════════════════════════════════════
"""
