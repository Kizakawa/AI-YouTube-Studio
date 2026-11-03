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
1. REGRA DE OURO: HOOK DE CURIOSIDADE "POR QUE..." OU PARADOXO MORTAL COM ENTIDADE EXPLÍCITA NO SEGUNDO 0,0s (6 A 12 PALAVRAS):
   - TÍTULO (`title`): DEVE conter os nomes exatos das entidades de alto volume de busca + gancho "Por Que / Como" ou impacto físico fácil de imaginar (ex: "Por Que a Onça-Pintada Foge Desse Porco do Mato? 🐆🐗", "Por Que o Guepardo Tem Medo do Avestruz? 🐆⚡", "Como a Sucuri Engole um Jacaré Inteiro no Rio 🐍🐊"). PROIBIDO usar unidades científicas secas como PSI, Newtons, kPa ou Joules!
   - HOOK DA CENA 1 (0,0s a 2,5s | 6 A 12 PALAVRAS — PRIORIZE O HOOK "POR QUE..." DE CURIOSIDADE IMEDIATA): No feed do Shorts, abrir o segundo 0,0s com uma pergunta contraintuitiva **"Por que + [Entidade A & B] + [Ação Chocante / Paradoxo]?"** (ou afirmação direta "Veja por que...") prende a atenção na hora porque o cérebro humano não consegue ignorar um "Por que" sem resposta! A Cena 1 DEVE nomear explicitamente a Entidade/Predador principal (e se o tema for `Entidade A vs Entidade B`, DEVE nomear AMBAS as entidades na Cena 1!). PROIBIDO fazer perguntas fechadas de Sim/Não ("Você sabia que...?", "Você já viu...?") e SEM unidades físicas abstratas como PSI ou Newtons!
   - PROIBIDO USAR APONTAMENTOS VAGOS QUE ESCONDEM O SUJEITO ("Olhe bem para este pequeno crânio...", "Veja o momento exato em que este predador...", "Você sabia que...") — esconder o nome do animal ou usar pergunta de Sim/Não na Cena 1 derruba a retenção!
2. AS 4 FÓRMULAS COMPROVADAS DE HOOK (>70% STAYED & >95% APV NO NOSSO CANAL):
   - [Arquétipo 1 (PRIORIDADE #1) — O Hook de Curiosidade "Por Que..." Contraintuitivo com Sujeito Explícito]:
     * "Por que uma onça-pintada sobe na árvore apavorada quando vê esse porco do mato?"
     * "Por que a mamba-negra mais mortal da África não consegue acertar esse pequeno mangusto?"
     * "Por que os cientistas trancaram esse buraco na Rússia a doze quilômetros de profundidade?"
   - [Arquétipo 2 — Confronto Direto / Paradoxo Letal (Vencedor 40.225 views `TXPjVAIaKWw` — 51,4% Stay, 79,1% APV)]:
     * "Esta mamba-negra mata em segundos, mas o mangusto ri do veneno!"
     * "A onça-pintada não sufoca o jacaré-açu—ela fura o crânio blindado dele na hora."
   - [Arquétipo 3 — O Último Erro com Sujeito Explícito (Vencedor `XoeQ1I_WHsI` — 51,3% Stay)]:
     * "O último erro de qualquer predador é desafiar as garras desse casuar."
     * "Esse búfalo de meia tonelada saiu andando, mas já está morto."
   - [Arquétipo 4 — Ameaça Direta em 2ª Pessoa com Nome da Entidade]:
     * "Se esse polvo-de-anéis-azuis tocar sua pele, você para de respirar em minutos."
3. PROIBIDO NO MERCADO BRASILEIRO:
   - NUNCA use frases vagas como "Olhe bem para..." ou "Veja o momento exato..." sem dizer o nome da espécie/fenômeno na Cena 1.
   - NUNCA divida um Short de 35 segundos em "Round 1, Round 2, Round 3" trocando para um terceiro animal que não está no título.
   - NUNCA faça perguntas fechadas de Sim/Não ("Você sabia que...?", "Você já viu...?") na Cena 1 — use perguntas abertas de alta curiosidade com **"Por que..."** ou **"Como..."**!
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
1. QUY TẮC VÀNG MỞ ĐẦU 0.0s: HOOK "TẠI SAO / VÌ SAO..." GÂY TÒ MÒ TỨC THÌ HOẶC NGHỊCH LÝ SINH TỬ NÊU ĐÍCH DANH THỰC THỂ NGAY Ở CẢNH 1:
   - TIÊU ĐỀ (`title`): Bắt buộc nêu đích danh thực thể + câu hỏi Tại Sao / Làm Sao hoặc hành động trực quan dễ hình dung. CẤM nhồi đơn vị vật lý khô khan như PSI, Newton, kPa!
   - CÂU HOOK CẢNH 1 (6–12 từ): Ưu tiên số 1 là mở đầu bằng **"Tại sao / Vì sao + [Thực thể A & B] + [Hành động ngược đời gây sốc]?"** hoặc câu khẳng định nghịch lý sinh tử ngay ở giây 0.0s! Bắt buộc nêu đích danh Thực thể chính (nếu chủ đề là cặp đấu `A vs B` thì BẮT BUỘC nêu tên CẢ 2 thực thể A và B ngay ở Cảnh 1!). TUYỆT ĐỐI CẤM câu hỏi Yes/No yếu ("Bạn có biết...?", "Bạn đã bao giờ...?") và CẤM chỉ trỏ mơ hồ giấu tên chủ thể.{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    else:
        return f"""
═══════════════════════════════════════════════════════════════════
🇺🇸 REGIONAL HOOK MASTERY — US / GLOBAL ENGLISH MARKET (en-US | TARGET: STAYED-TO-WATCH >= 75%)
═══════════════════════════════════════════════════════════════════
1. GOLDEN 0.0s RETENTION RULE: OPEN-LOOP "WHY..." CURIOSITY PARADOX OR VISCERAL CLASH NAMING EXPLICIT ENTITIES IN SCENE 1 (6 TO 12 WORDS | GRADE-5 ENGLISH):
   - TITLE (`title`): MUST explicitly name the high-search-volume primary entities in a natural conversational "Why / How" or visceral stakes title (e.g., "Why Great White Sharks Are Terrified Of Orcas 🐋🦈", "Why Deadly Black Mambas Can't Kill A Mongoose 🦦🐍", "How Sea Turtles Eat Deadly Jellyfish Like Noodles 🐢🪼"). STRICTLY BAN dry textbook physics units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`) that ordinary viewers cannot picture!
   - SCENE 1 HOOK (0.0s–2.5s, 6–12 words — PRIORITIZE THE "WHY..." CURIOSITY PARADOX HOOK): Opening the first 0.0s with a counter-intuitive **"Why + [Named Entity A & B] + [Shocking / Counter-Intuitive Action]?"** (or **"Here's why [Entity A]..."**) creates an instant psychological open loop that forces viewers to stay and find out the answer! Scene 1 MUST explicitly name the primary entity/combatants (and if the topic pits `Entity A vs Entity B`, Scene 1 MUST explicitly name BOTH `Entity A` AND `Entity B` right in Scene 1!). NEVER hide the second entity and NEVER use weak Yes/No questions (`"Did you know...?"`, `"Have you ever seen...?"`) or dry science units!
   - STRICTLY FORBIDDEN VAGUE POINTERS: NEVER start Scene 1 with vague entity-blind pointers like `"Look closely at why you cannot find the exit"`, `"Look closely at these deadly floating blue tentacles"`, `"You'd think a massive whale wouldn't fear anything"`, or `"Did you know..."`!
2. THE 4 PROVEN HIGH-RETENTION US HOOK ARCHETYPES (CHOOSE THE #1 BEST FIT FOR SCENE 1 IN 6–12 WORDS):
   - [Archetype 1 (PRIORITY #1 SIGNATURE HOOK) — The Counter-Intuitive "Why..." Curiosity Paradox Naming Explicit Entities]:
     * "Why do massive great white sharks panic and flee the second an orca shows up?"
     * "Why can a sea turtle chew up venomous box jellyfish without feeling a thing?"
     * "Why do FBI agents place one paper water cup six inches from a guilty suspect?"
     * "Why did Soviet commanders refuse to turn the final nuclear launch key in 1962?"
   - [Archetype 2 — Direct Scale / 2-Entity Extreme Clash (Proven Winner `A-oNKdpL0rM` 1,146 views & `w7iUy-8hjVo` 47.7% Stay)]:
     * "Two miles down, a colossal squid's rotating hooks rip straight into a hunting sperm whale."
     * "A jaguar doesn't choke an armored caiman—it punches straight through its skull."
   - [Archetype 3 — Walking Dead / Lethal Paradox Naming Both Entities (Proven Winner `1NeB0Qe6_t4` 1,325 views in 12h & `D_sIws9Buto` 123.8% APV)]:
     * "This twelve-hundred-pound buffalo walked away from a Komodo dragon, already dead."
     * "Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."
   - [Archetype 4 — Named Psychological / Historical Shock Event]:
     * "Las Vegas casinos remove every clock and window to trap your brain's dopamine."
     * "One wrong turn by Archduke Franz Ferdinand's driver in 1914 erased four empires."
3. STRICTLY BANNED IN US SHORTS:
   - NO vague `"Look closely at..."` or `"Watch the exact moment..."` hooks that hide the primary subject in Scene 1.
   - NO multi-round gauntlets (`"Round One... Round Two... Round Three..."`) or bait-and-switch 3rd animals inside a 35-second Short: all 7–8 scenes MUST stay 100% focused on the exact entities promised in the `title`!
   - NO abstract poetry ("loaded weapon", "silent cage"), and NO passive Yes/No questions ("Did you know...?", "Have you ever...?"). Open-loop `"Why..."` / `"How..."` / `"What if..."` curiosity hooks ARE strongly encouraged!
4. 1-1 VISUAL CONTRAST & LEAD-SUBJECT ALTERNATION RULE (BREAKING THE ~1,000-VIEW SEED COHORT CEILING):
   - Real YouTube Analytics v2 telemetry proves that starting consecutive uploads with the exact same lead subject/species noun or identical opening visual color palette triggers Audience Cohort Fatigue (Stayed-to-Watch drops from ~48.5% to ~32.8%, capping views at ~1,000).
   - NEVER start the new `title` or Scene 1 visual hook with the same primary subject noun/entity that led the channel's last 2-3 videos! Always alternate visual palettes and foreground the freshest, most visually contrasting phenomenon or opponent.
5. ORGANIC 50/50 COUNTERFACTUAL DEBATE COMMENT (`pinned_comment` — ZERO 'SUBSCRIBE' SPAM):
   - NEVER ask a question in `pinned_comment` that the video already answered (e.g. if the video already showed why the Mongoose beats the Black Mamba, DO NOT ask "Who wins: Mongoose or Black Mamba?").
   - NEVER append robotic promotional footers like "🔔 SUBSCRIBE for more episodes!".
   - Write a 1-sentence Counterfactual Twist / Escalated Odds / Next-Opponent Dilemma (12 to 22 words) ending with `(1) ... or (2) ... 👇` that splits the comments 50/50 (e.g. "Be honest: if it were 2 Black Mambas at the same time, does the Mongoose still survive? (1) Yes or (2) No chance? 👇").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
