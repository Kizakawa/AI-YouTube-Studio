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
    dựa trên dữ liệu phân tích 976 video YouTube Shorts triệu view (1M–371M views) từ YouTube Data API v3 + Transcript WPM,
    kết hợp Quy tắc Vàng Phân Vai: Title (YT_SEARCH) vs. Scene 1 Hook (4 Đòn Tâm Lý Thị Giác 0.0s).
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
1. REGRA DE OURO: SEPARAÇÃO ENTRE TÍTULO (BUSCA YT_SEARCH) VS. HOOK DA CENA 1 (RETENÇÃO VISUAL 0,0s):
   - TÍTULO (`title`): DEVE conter os nomes exatos das entidades de alto volume de busca + métrica física extrema (`[Entidade A] vs [Entidade B]: [Métrica Extrema]`, ex: "Lula-Colossal vs Cachalote: O Duelo a 2.000m 🦑🐋", "Onça-Pintada vs Jacaré: A Mordida de 1.500 PSI 🐆🐊").
   - HOOK DA CENA 1 (0,0s a 2,2s | 6 A 10 PALAVRAS): NUNCA repita o título como um robô lendo estatísticas! Se o público acabou de ler "1.500 PSI" no título e a voz repete "Com 1.500 PSI a onça morde o jacaré" no segundo 0, o mistério morre e a pessoa desliza! Guarde o número exato (PSI, metros, decibéis) para o Ato 2 (Cenas 2–3) e Ato 3 (Cena 4 Twist).
2. AS 4 FÓRMULAS PSICOLÓGICAS E VISUAIS DE HOOK (+10 MILHÕES DE VIEWS — ESCOLHA A MELHOR PARA A CENA 1):
   - [Arquétipo 1 — Apontamento Micro-Visual / Camuflagem ("Olhe bem para...")]: Obrigue o olho do espectador a procurar um detalhe estranho ou ameaça escondida na tela nos primeiros 3 segundos:
     * "Olhe bem para essas marcas redondas na pele desse cachalote."
     * "O que parece um tronco podre nessa água rasa acabou de abrir os olhos."
     * "Repare na sombra subindo devagar atrás desse mergulhador."
   - [Arquétipo 2 — O Último Erro / Caçador Vira Presa ("O último erro...")]: Comece no exato instante em que um predador ou pessoa comete um erro fatal sem perceber:
     * "O último erro desse jacaré foi subir nesse barranco sem olhar para cima."
     * "Esse tubarão entrou no território do único animal que ele teme."
     * "Tocar nessa parede da caverna foi o erro mais caro da ciência."
   - [Arquétipo 3 — Simulação de Sobrevivência em 2ª Pessoa ("Se você vir isso...")]: Coloque o espectador dentro do perigo usando "Se você...":
     * "Se um submarino tocar nessa linha branca no fundo do mar, ele não volta."
     * "Se você vir ondas quadradas na praia, saia da água imediatamente."
   - [Arquétipo 4 — Quebra de Expectativa / Inversão de Força]: Mostre uma situação que desafia a lógica:
     * "Seis leões famintos cercaram esse pequeno animal, mas os leões estão fugindo."
     * "Ninguém imaginava o que estava escondido a dois mil metros sob o gelo."
3. PROIBIDO NO MERCADO BRASILEIRO:
   - NUNCA repita o título palavra por palavra na Cena 1 nem entregue o resultado final no segundo 0.
   - NUNCA comece com frases lentas de documentário de TV, metáforas poéticas ou rótulos de dicionário sem verbo ativo ("Nome-do-Animal: um soco a 80 km/h").
   - NUNCA faça perguntas passivas ("Você sabia que...?").
4. REGRA DE ALTERNÂNCIA VISUAL 1-1 & ANTI-FADIGA DE TEMA (QUEBRA DO TETO DE ~1.000 VIEWS):
   - Dados reais da YouTube Analytics API v2 provam que repetir o mesmo animal/sujeito principal no início do Título e na paleta visual das primeiras cenas em vídeos consecutivos causa fadiga imediata no público do canal (Stayed-to-Watch cai de ~48,5% para ~32,8%, travando o vídeo no lote inicial de ~1.000 views).
   - NUNCA inicie o Título (`title`) ou a Cena 1 com o mesmo sujeito/espécie que já liderou os últimos 2-3 vídeos recentes do canal! Se o tema envolver um fenômeno extremo (vulcão a 100°C, cratera a 1.000°C, lago tóxico, anomalia acústica) + um animal já usado recentemente, destaque SEMPRE o fenômeno extremo inédito ou o oponente novo na abertura do Título e na cor/visual da Cena 1!
5. GATILHO DE DEBATE ORGÂNICO 50/50 NO COMENTÁRIO FIXADO (`pinned_comment` — ZERO SPAM DE 'INSCREVA-SE'):
   - NUNCA pergunte no `pinned_comment` algo que o vídeo já acabou de responder (ex: se o vídeo já mostrou o Mangusto vencendo a Mamba-Negra, NÃO pergunte "Quem vence: Mangusto ou Mamba-Negra?").
   - NUNCA adicione frases robóticas de propaganda como "🔔 INSCREVA-SE no canal para não perder o próximo episódio!".
   - Crie uma Hipótese Contrafactual de Escalonamento (2 contra 1, Mudança de Terreno ou Próximo Desafiante) curta (12 a 22 palavras) que divida os comentários 50/50 (ex: "Honestamente: e se fossem 2 Mambas-Negras ao mesmo tempo, o Mangusto ainda sobreviveria? (1) Sim ou (2) Sem chance? 👇").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    elif is_vi:
        return f"""
═══════════════════════════════════════════════════════════════════
🇻🇳 REGIONAL HOOK MASTERY — THỊ TRƯỜNG VIỆT NAM (vi-VN | MỤC TIÊU: STAYED-TO-WATCH >= 70%)
═══════════════════════════════════════════════════════════════════
1. QUY TẮC VÀNG PHÂN VAI: TIÊU ĐỀ (ĂN TÌM KIẾM) VS. CÂU HOOK CẢNH 1 (KHÓA MẮT NGƯỜI XEM 0.0s):
   - TIÊU ĐỀ (`title`): Bắt buộc nêu đích danh thực thể + con số cực hạn (`[Thực thể A] vs [Thực thể B]: [Con số]`).
   - CÂU HOOK CẢNH 1 (6–10 từ): TUYỆT ĐỐI KHÔNG đọc lặp lại tiêu đề hay đọc thông số khô khan ở giây 0.0s! Hãy để dành con số cho Cảnh 2–3 và Cảnh 4, còn Cảnh 1 phải dùng 1 trong 4 Đòn Tâm Lý Thị Giác: (1) Chỉ tay vào chi tiết lạ trên màn hình ("Hãy nhìn kỹ vết sẹo tròn trên đầu con cá nhà táng này..."), (2) Sai lầm cuối cùng ("Sai lầm cuối cùng của con cá sấu này là..."), (3) Cảnh báo sinh tồn ngôi thứ 2 ("Nếu bạn nhìn thấy vệt nước này..."), hoặc (4) Phá vỡ định kiến.
   - Tuyệt đối không mở đầu bằng câu hỏi thụ động ("Bạn có biết...") hay văn phong sách giáo khoa.{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    else:
        return f"""
═══════════════════════════════════════════════════════════════════
🇺🇸 REGIONAL HOOK MASTERY — US / GLOBAL ENGLISH MARKET (en-US | TARGET: STAYED-TO-WATCH >= 70%)
═══════════════════════════════════════════════════════════════════
1. GOLDEN SEPARATION OF DUTY: SEARCHABLE TITLE VS. 0.0s SCENE 1 CURIOSITY HOOK (6 TO 10 WORDS | GRADE-5 ENGLISH):
   - TITLE (`title`): MUST explicitly name the high-search-volume primary entities + extreme physical metric or confrontation (`[Entity A] vs [Entity B]: [Extreme Metric]`, e.g., "Green Anaconda vs. Black Caiman: 1,500 PSI River Duel 🐍🐊", "The 93°C Boiling Amazon River That Cooks Everything 🔥🌊").
   - SCENE 1 HOOK (0.0s–2.2s): NEVER repeat the Title verbatim in Scene 1, and NEVER spoil the final outcome or recite a dry textbook statistic at 0.0s! If the viewer reads "1,500 PSI" in the title and the voiceover immediately repeats "This jaguar crushes skulls with 1,500 PSI," the curiosity gap dies in 1.5 seconds! Save the hard numerical metric for Act 2 (Scenes 2–3) and Act 3 (Scene 4 Mid-Video Twist).
2. THE 4 MILLION-VIEW US PSYCHOLOGICAL-VISUAL HOOK ARCHETYPES (CHOOSE THE #1 BEST FIT FOR SCENE 1 IN 6–10 WORDS):
   - [Archetype 1 — Visual Micro-Observation / Camouflage Pointer ("Look closely at...")]: Force the viewer's eyes to scan the frame for a hidden threat, strange scar, or micro-expression during the first 3 seconds:
     * "Look closely at the circular scars on this whale's head."
     * "What looks like a dead tree branch in this river just opened its eyes."
     * "Watch this suspect's hands the exact second the detective mentions the car."
   - [Archetype 2 — The Fatal Mistake / In Medias Res Trap]: Drop the viewer into the exact moment a predator, army, or person makes a lethal miscalculation (Casual Geographic / HistoryMarche 5.33M Formula):
     * "This caiman's last mistake was climbing onto this riverbank without looking up."
     * "The Roman legions pushed forward, walking straight into Hannibal's hidden riverbed trap."
     * "Drinking from this crystal-clear desert pool was a fatal mistake."
   - [Archetype 3 — 2nd-Person Survival / Real-Life Spoken Trap ("If you ever..." / Quoted Insult)]: Put the viewer inside the danger or open with a real-life manipulation quote (Charisma on Command 9.78M / Zack D. Films 15.7M Formula):
     * "Saying 'you actually look good today' is a hidden insult—watch their eyes next."
     * "If a submarine touches this white line on the seafloor, it never surfaces."
     * "If someone touches their neck while apologizing, watch what their eyes do next."
   - [Archetype 4 — The Illusion Breaker / Time-Anchored Emotional Shock]: Present a visual paradox or concrete abandonment duration (Casual Geographic 8.6M / GeoBeats Animals 2.77M Formula):
     * "Six starving lions surrounded this tiny animal, and the lions are running away."
     * "He spent ten years completely alone in a concrete cage without a soft bed."
     * "Everyone walked past this muddy canal drain, not realizing two eyes were staring back."
3. STRICTLY BANNED IN US HOOKS:
   - NO verbatim Title repetition in Scene 1, NO dry Wikipedia metric recitation at 0.0s, NO abstract poetry ("loaded weapon", "silent cage"), and NO passive Yes/No questions ("Did you know...?").
4. 1-1 VISUAL CONTRAST & LEAD-SUBJECT ALTERNATION RULE (BREAKING THE ~1,000-VIEW SEED COHORT CEILING):
   - Real YouTube Analytics v2 telemetry proves that starting consecutive uploads with the exact same lead subject/species noun or identical opening visual color palette triggers Audience Cohort Fatigue (Stayed-to-Watch drops from ~48.5% to ~32.8%, capping views at ~1,000).
   - NEVER start the new `title` or Scene 1 visual hook with the same primary subject noun/entity that led the channel's last 2-3 videos! Always alternate visual palettes and foreground the freshest, most visually contrasting phenomenon or opponent.
5. ORGANIC 50/50 COUNTERFACTUAL DEBATE COMMENT (`pinned_comment` — ZERO 'SUBSCRIBE' SPAM):
   - NEVER ask a question in `pinned_comment` that the video already answered (e.g. if the video already showed why the Mongoose beats the Black Mamba, DO NOT ask "Who wins: Mongoose or Black Mamba?").
   - NEVER append robotic promotional footers like "🔔 SUBSCRIBE for more episodes!".
   - Write a 1-sentence Counterfactual Twist / Escalated Odds / Next-Opponent Dilemma (12 to 22 words) ending with `(1) ... or (2) ... 👇` that splits the comments 50/50 (e.g. "Be honest: if it were 2 Black Mambas at the same time, does the Mongoose still survive? (1) Yes or (2) No chance? 👇").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
