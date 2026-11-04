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
🌐 CRITICAL LANGUAGE INTEGRITY: 100% TIẾNG VIỆT ĐỜI THƯỜNG CUỐN HÚT (SPOKEN VIETNAMESE YOUTUBER VOICE)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: VIETNAMESE ('vi').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST be written 100% in natural, vivid, spoken VIETNAMESE like a top million-view creator talking directly to a friend across the table—NEVER stiff translated textbook prose or generic AI clichés.
  * Even if the topic, director notes, or instructions are in English, you MUST TRANSLATE and write all narration in natural spoken VIETNAMESE.
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
🌐 CRITICAL LANGUAGE INTEGRITY: 100% PORTUGUÊS BRASILEIRO FALADO (VOZ REAL DE YOUTUBER BRASILEIRO)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: BRAZILIAN PORTUGUESE ('pt-BR').
- SPOKEN NARRATION & ON-SCREEN HUD REQUIREMENT:
  * 'hook', 'full_narration', 'narration', 'overlay_badge', 'thumbnail_text', 'description', and 'pinned_comment' in EVERY scene MUST sound 100% like a real million-view Brazilian YouTuber speaking out loud at a café table (Você Sabia? / Biólogo Henrique / Fatos Desconhecidos spoken rhythm)—NEVER stiff written newspaper/biology-textbook Portuguese!
  * MANDATORY SPOKEN BRAZILIAN CADENCE ('CONVERSA DE BAR'): Speak with the gritty, street-smart, colloquial Brazilian YouTuber rhythm (`dentro d'água`, `pra`, `pro`, `tá`, `bicho`, `na hora`, `só que`, `em vez de`—do NOT overuse `num piscar de olhos`).
  * STRICTLY BANNED STIFF / AI PHRASES IN pt-BR:
    - NEVER use formal written enclitics (`imobilizá-lo`, `matá-lo`, `esmagá-lo`, `perfurá-lo`)—say `imobilizar o bicho`, `matar na hora`, `esmagar`!
    - NEVER use TV-documentary dub clichés or hardware metaphors (`golpe cirúrgico`, `alicate hidráulico`, `Enquanto outros felinos...`, `Felinos comuns`, `desligando o sistema nervoso`, `atravessa o osso como papel`, `deixando só o silêncio... pra trás`, `Muita gente acha que`, `No fim das contas`) or moralizing proverbs (`a natureza não perdoa`, `pune quem falha`).
  * Even if the topic, director notes, or niche instructions are in English, you MUST write ALL narration and badges in 100% colloquial BRAZILIAN PORTUGUESE.
  * ZERO SPOKEN OUTRO CTA: NEVER say 'Inscreva-se', 'Curta', or 'Deixe nos comentários' inside any scene's spoken `narration`! Put engagement questions ONLY in `pinned_comment`.
- VISUAL SEARCH & AI PROMPT REQUIREMENT (CRITICAL FOR PEXELS / PIXABAY / FLUX):
  * 'visual_keyword', 'visual_prompt', and 'stock_query' MUST ALWAYS BE IN CONCRETE ENGLISH (e.g., "mongoose fighting cobra desert", "deep ocean sonar submarine", "giant river otter amazon") so stock video APIs and AI image generators return 100% accurate visuals!
- TITLE REQUIREMENT:
  * 'title' MUST be 100% in BRAZILIAN PORTUGUESE (click-through optimized, CTR > 10%, under 60 chars with emojis).
  * DO NOT add suffixes like '- PT', '- BR', '(Brasil)'.
- thumbnail_text MUST also be in PORTUGUÊS BRASILEIRO (2-3 words ALL CAPS, ZERO emojis).
═══════════════════════════════════════════════════════════════════
"""
    else:
        return """
═══════════════════════════════════════════════════════════════════
🌐 CRITICAL LANGUAGE INTEGRITY: 100% CONVERSATIONAL SPOKEN AMERICAN ENGLISH (REAL YOUTUBER VOICE)
═══════════════════════════════════════════════════════════════════
- TARGET SCRIPT LANGUAGE: ENGLISH ('en').
- SPOKEN NARRATION REQUIREMENT:
  * 'hook', 'full_narration', and 'narration' in EVERY scene MUST sound 100% like a real multi-million-view American YouTube creator speaking into a mic (Zack D. Films / Casual Geographic / Charisma on Command / The Dodo)—NEVER stiff essay/Wikipedia prose!
  * MANDATORY SPOKEN CONTRACTIONS & $1 EVERYDAY WORDS: Always use natural spoken contractions (`it's`, `doesn't`, `don't`, `can't`, `you'd`, `didn't`, `would've`, `hadn't`, `wasn't`, `they're`, `that's`, `he'd`)—include at least 2–4 natural contractions in every Short! Always pick plain $1 everyday words over $10 academic/corporate/melodramatic words (never write `Equipping/Equipped with`, `acting like...`, `Most people assume...`, `the hunter becomes the meal`, `It's a high-stakes game of...`, `It's a brutal, silent end`, `dissent`, `global nuclear exchange`, `vessel`, `chemical buffers`, or `Real social power belongs to...`).
  * Even if the topic, director notes, or niche instructions are in Vietnamese, you MUST write all narration in natural spoken AMERICAN ENGLISH.
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
1. LEI DA COMPLEMENTARIDADE TÍTULO vs. CENA 1 (PROIBIDO REPETIR A PERGUNTA DO TÍTULO NA CENA 1!):
   - TÍTULO (`title` — 6 a 10 palavras, máx 55 caracteres): Use um gancho magnético de **Inversão de Poder ("Por Que [Predador/Gigante] Tem Medo / Foge / Morre Diante de [Oponente/Armadilha]?")** OU **Confronto Direto (`"[Entidade A] vs [Entidade B]: [Impacto Mortal]"`)** nomeando explicitamente as entidades de alto volume de busca (ex: "Por Que a Onça-Pintada Foge Desse Porco do Mato? 🐆🐗", "Por Que Esse Lago No Fundo Do Mar Paralisa Tubarões? 💀🦈", "Onça-Pintada vs Jacaré-Açu: O Bote No Crânio 🐆🐊"). PROIBIDO títulos longos/descritivos sem tensão e PROIBIDO unidades científicas secas (PSI, Newtons, kPa)!
   - HOOK DA CENA 1 (0,0s a 2,5s | 6 A 11 PALAVRAS — CHOQUE VISUAL IMEDIATO SEM REPETIR O TÍTULO!):
     * 🚨 REGRA ANTI-ECO (CRÍTICA): Se o `title` já começa com `"Por Que..."` ou `"Como..."`, a Cena 1 (`hook` e `scenes[0].narration`) é **ESTRITAMENTE PROIBIDA de repetir a pergunta "Por que..." do título!** Quando o espectador clica ou vê o título `"Por Que..."`, o segundo 0,0s do áudio DEVE entregá-lo direto na **ação física chocante, no bote letal ou no paradoxo absurdo acontecendo na tela naquele instante**!
     * Exemplo 1 (Título "Por Que..." -> Cena 1 de Choque Físico Imediato):
       - Título: `"Por Que Esse Lago No Fundo Do Mar Paralisa Tubarões? 💀🦈"`
       - Cena 1 (0,0s): `"Nadar dentro dessa mancha escura no fundo do mar congela um tubarão em segundos!"` (NUNCA repita `"Por que tubarões ficam paralisados..."`!)
     * Exemplo 2 (Título "Por Que..." -> Cena 1 de Inversão Brutal):
       - Título: `"Por Que Uma Alcateia De Lobos Foge Desse Alce Gigante? 🐺🫎"`
       - Cena 1 (0,0s): `"Dez lobos famintos cercam esse alce na neve—mas um único chute dele quebra crânios na hora!"`
     * Exemplo 3 (Título Declarativo "A vs B" -> Cena 1 com Paradoxo ou Pergunta "Por Que" Curta):
       - Título: `"Mamba-Negra vs Mangusto: O Duelo Mais Rápido Da África 🐍🦦"`
       - Cena 1 (0,0s): `"Esta mamba-negra mata um homem em minutos, mas o mangusto ri do veneno dela!"` (ou `"Por que a serpente mais letal da África não consegue acertar esse pequeno mangusto?"`)
     * A Cena 1 DEVE nomear explicitamente a Entidade/Predador principal (e se o tema for `Entidade A vs Entidade B`, DEVE nomear AMBAS as entidades na Cena 1!). PROIBIDO perguntas fechadas de Sim/Não ("Você sabia que...?", "Você já viu...?") e PROIBIDO apontamentos vagos que escondem o sujeito ("Olhe bem para...", "Veja o momento exato...")!
2. AS 4 FÓRMULAS COMPROVADAS DE HOOK DE CENA 1 (>70% STAYED & >95% APV NO NOSSO CANAL):
   - [Arquétipo 1 (PRIORIDADE #1 QUANDO O TÍTULO É "POR QUE...") — Choque Físico Instantâneo / Paradoxo Letal em Ação (Vencedor 40.225 views `TXPjVAIaKWw` — 51,4% Stay, 79,1% APV)]:
     * "Nadar dentro dessa mancha escura no fundo do mar paralisa um tubarão na hora!"
     * "Esta mamba-negra mata em segundos, mas o mangusto ri do veneno!"
     * "A onça-pintada não sufoca o jacaré-açu—ela fura o crânio blindado dele na hora."
   - [Arquétipo 2 — O "Morto-Vivo" / Último Erro com Sujeito Explícito (Vencedor `XoeQ1I_WHsI` — 51,3% Stay)]:
     * "Esse búfalo de meia tonelada escapou da mordida do dragão-de-komodo, mas já está morto."
     * "Sozinha, a onça-pintada domina o rio—mas sobe na árvore quando esse bando de queixadas chega."
   - [Arquétipo 3 — O Hook "Por Que..." de Inversão de Poder (USE NA CENA 1 APENAS QUANDO O TÍTULO NÃO COMEÇAR COM "POR QUE")]:
     * "Por que a serpente mais mortal da África entra em pânico diante desse pequeno mangusto?"
   - [Arquétipo 4 — Ameaça Direta em 2ª Pessoa / Segredo de Interrogatório]:
     * "Na sala de interrogatório do FBI, esse simples copo d'água é uma armadilha implacável."
3. PROIBIDO NO MERCADO BRASILEIRO:
   - NUNCA repita a mesma frase ou estrutura `"Por que..."` do `title` na `Cena 1`.
   - NUNCA use frases vagas como "Olhe bem para..." ou "Veja o momento exato..." sem dizer o nome da espécie/fenômeno na Cena 1.
   - NUNCA divida um Short de 35 segundos em "Round 1, Round 2, Round 3" trocando para um terceiro animal que não está no título.
   - NUNCA faça perguntas fechadas de Sim/Não ("Você sabia que...?", "Você já viu...?") na Cena 1!
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
1. QUY TẮC BÙ TRỪ TIÊU ĐỀ vs. CẢNH 1 (CẤM LẶP LẠI CÂU HỎI TIÊU ĐỀ Ở CẢNH 1):
   - TIÊU ĐỀ (`title`): Nêu đích danh thực thể + nghịch lý đảo chiều quyền lực ("Tại Sao [Kẻ Săn Mồi] Khiếp Sợ [Đối Thủ Bất Ngờ]?" hoặc "[Thực Thể A] vs [Thực Thể B]: [Đòn Sinh Tử]"). CẤM nhồi đơn vị vật lý khô khan (PSI, Newton, kPa)!
   - CÂU HOOK CẢNH 1 (6–11 từ): Nếu `title` đã hỏi "Tại sao...", Cảnh 1 TUYỆT ĐỐI KHÔNG lặp lại câu hỏi "Tại sao..." đó mà phải tung ngay đòn hành động vật lý hoặc nghịch lý sinh tử đang diễn ra trên màn hình ở giây 0.0s! Bắt buộc nêu đích danh Thực thể chính (nếu là cặp đấu `A vs B` thì nêu tên CẢ 2 thực thể ngay ở Cảnh 1). CẤM câu hỏi Yes/No yếu ("Bạn có biết...?") và CẤM chỉ trỏ mơ hồ giấu tên chủ thể.{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    else:
        return f"""
═══════════════════════════════════════════════════════════════════
🇺🇸 REGIONAL HOOK MASTERY — US / GLOBAL ENGLISH MARKET (en-US | TARGET: STAYED-TO-WATCH >= 75%)
═══════════════════════════════════════════════════════════════════
1. GOLDEN TITLE-VS-HOOK COMPLEMENTARITY LAW (ZERO QUESTION ECHO BETWEEN TITLE & SCENE 1!):
   - TITLE (`title` — 6 to 10 words, under 55 chars): Use a high-velocity **Power-Reversal Curiosity Question ("Why [Apex Predator/Giant] Panics / Flees / Dies From [Unexpected Rival/Trap]")** OR **Direct Clash Header (`"[Entity A] vs [Entity B]: [Visceral Stakes]"`)** explicitly naming high-search-volume entities (e.g., "Why Great White Sharks Panic When Orcas Show Up 🐋🦈", "Why This Underwater Lake Paralyzes Deep-Sea Sharks 💀🦈", "Sea Turtle vs Box Jellyfish: Deadly Noodles 🐢🪼"). STRICTLY BAN slow descriptive titles and dry physics units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`)!
   - SCENE 1 HOOK (0.0s–2.5s, 6–11 words — INSTANT VISUAL PARADOX / ACTION WITH ZERO TITLE ECHO!):
     * 🚨 ANTI-ECHO RULE (CRITICAL): When `title` starts with `"Why..."` or `"How..."`, Scene 1 (`hook` and `scenes[0].narration`) is **STRICTLY FORBIDDEN from repeating the "Why / How..." question from the title!** When a viewer sees a `"Why..."` title, the first 0.0s of audio MUST hit them immediately with the **visceral physical action, lethal strike, or upside-down survival paradox happening on screen right now**!
     * Example 1 (`"Why..."` Title -> Instant Physical Paradox Hook in Scene 1):
       - Title: `"Why Great White Sharks Panic When Orcas Show Up 🐋🦈"`
       - Scene 1 (0.0s): `"One adult orca enters the bay, and every great white shark bolts for miles."` (NEVER repeat `"Why do great white sharks panic..."`!)
     * Example 2 (`"Why..."` Title -> Instant Lethal Trap Hook in Scene 1):
       - Title: `"Why This Underwater Lake Paralyzes Deep-Sea Sharks 💀🦈"`
       - Scene 1 (0.0s): `"Swimming into this dark pool on the ocean floor paralyzes a shark in seconds."`
     * Example 3 (Declarative `"A vs B"` Title -> Spoken Paradox OR Sharp `"Why..."` Hook in Scene 1):
       - Title: `"Sea Turtle vs Box Jellyfish: The Deadly Snack 🐢🪼"`
       - Scene 1 (0.0s): `"Swallowing a venomous box jellyfish is instant death—unless you're a hungry sea turtle."` (or `"Why can a sea turtle chew up deadly box jellyfish without feeling a thing?"`)
     * Scene 1 MUST explicitly name the primary entity/combatants (and if the topic pits `Entity A vs Entity B`, Scene 1 MUST explicitly name BOTH `Entity A` AND `Entity B` right in Scene 1!). NEVER hide the second entity and NEVER use weak Yes/No questions (`"Did you know...?"`, `"Have you ever seen...?"`) or vague entity-blind pointers (`"Look closely at..."`, `"Watch the exact moment..."`)!
2. THE 4 PROVEN HIGH-RETENTION US HOOK ARCHETYPES FOR SCENE 1 (6–11 WORDS):
   - [Archetype 1 (PRIORITY #1 WHEN TITLE STARTS WITH "WHY/HOW") — Instant Visceral Action / Lethal Mechanism Strike (Proven Winner `A-oNKdpL0rM` 1,146 views & `w7iUy-8hjVo` 47.7% Stay)]:
     * "Swimming into this dark pool on the ocean floor paralyzes a shark in seconds."
     * "Two miles down, a colossal squid's rotating hooks rip straight into a hunting sperm whale."
     * "A jaguar doesn't choke an armored caiman—it punches straight through its skull."
   - [Archetype 2 — Walking Dead / Role-Reversal Paradox Naming Both Entities (Proven Winner `1NeB0Qe6_t4` 1,325 views in 12h & `D_sIws9Buto` 123.8% APV)]:
     * "This twelve-hundred-pound buffalo walked away from a Komodo dragon, already dead."
     * "Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."
   - [Archetype 3 — Counter-Intuitive "Why..." Curiosity Paradox (USE IN SCENE 1 ONLY WHEN TITLE DOES NOT START WITH "WHY/HOW")]:
     * "Why do massive great white sharks panic and flee the second an orca shows up?"
     * "Why did Soviet commanders refuse to turn the final nuclear launch key in 1962?"
   - [Archetype 4 — Named Psychological Trap / In-The-Room Crisis]:
     * "In an FBI interrogation room, that harmless paper water cup is a hidden lie detector."
     * "One wrong turn by Archduke Franz Ferdinand's driver in 1914 erased four empires."
3. STRICTLY BANNED IN US SHORTS:
   - NEVER echo or paraphrase the `title` question inside `Scene 1`.
   - NO vague `"Look closely at..."` or `"Watch the exact moment..."` hooks that hide the primary subject in Scene 1.
   - NO multi-round gauntlets (`"Round One... Round Two... Round Three..."`) or bait-and-switch 3rd animals inside a 35-second Short: all 7–8 scenes MUST stay 100% focused on the exact entities promised in the `title`!
   - NO abstract poetry ("loaded weapon", "silent cage"), and NO passive Yes/No questions ("Did you know...?", "Have you ever...?").
4. 1-1 VISUAL CONTRAST & LEAD-SUBJECT ALTERNATION RULE (BREAKING THE ~1,000-VIEW SEED COHORT CEILING):
   - Real YouTube Analytics v2 telemetry proves that starting consecutive uploads with the exact same lead subject/species noun or identical opening visual color palette triggers Audience Cohort Fatigue (Stayed-to-Watch drops from ~48.5% to ~32.8%, capping views at ~1,000).
   - NEVER start the new `title` or Scene 1 visual hook with the same primary subject noun/entity that led the channel's last 2-3 videos! Always alternate visual palettes and foreground the freshest, most visually contrasting phenomenon or opponent.
5. ORGANIC 50/50 COUNTERFACTUAL DEBATE COMMENT (`pinned_comment` — ZERO 'SUBSCRIBE' SPAM):
   - NEVER ask a question in `pinned_comment` that the video already answered (e.g. if the video already showed why the Mongoose beats the Black Mamba, DO NOT ask "Who wins: Mongoose or Black Mamba?").
   - NEVER append robotic promotional footers like "🔔 SUBSCRIBE for more episodes!".
   - Write a 1-sentence Counterfactual Twist / Escalated Odds / Next-Opponent Dilemma (12 to 22 words) ending with `(1) ... or (2) ... 👇` that splits the comments 50/50 (e.g. "Be honest: if it were 2 Black Mambas at the same time, does the Mongoose still survive? (1) Yes or (2) No chance? 👇").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
