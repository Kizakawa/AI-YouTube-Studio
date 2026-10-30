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
1. REGRA DE OURO: ENTIDADE EXPLÍCITA + IMPACTO VISCERAL IMAGINÁVEL OU PARADOXO MORTAL NO SEGUNDO 0,0s (6 A 10 PALAVRAS):
   - TÍTULO (`title`): DEVE conter os nomes exatos das entidades de alto volume de busca + ação física visceral ou confronto fácil de imaginar (`[Entidade A] vs [Entidade B]: [Impacto Visceral]`, ex: "Mamba-Negra vs Mangusto: Golpe Mais Rápido Que um Piscar! 🐍🥊", "Onça-Pintada vs Jacaré: A Mordida Que Fura o Crânio! 🐆🐊"). PROIBIDO usar unidades científicas secas como PSI, Newtons, kPa ou Joules!
   - HOOK DA CENA 1 (0,0s a 2,2s | 6 A 10 PALAVRAS): No feed do Shorts, o público NÃO lê o título antes de ouvir os 2 primeiros segundos! Por isso, a Cena 1 DEVE nomear explicitamente a Entidade/Predador principal (ou o confronto A vs B) + uma imagem física brutal, comparação do dia a dia ou paradoxo mortal logo na primeira frase (SEM unidades físicas abstratas como PSI ou Newtons que o público não consegue visualizar)!
   - PROIBIDO USAR APONTAMENTOS VAGOS QUE ESCONDEM O SUJEITO ("Olhe bem para este pequeno crânio...", "Veja o momento exato em que este predador...") — dados reais provam que esconder o nome do animal na Cena 1 derruba a retenção para <17%!
2. AS 4 FÓRMULAS COMPROVADAS DE HOOK (>50% STAYED & >75% APV NO NOSSO CANAL):
   - [Arquétipo 1 — Confronto Direto / Paradoxo Letal (Vencedor 40.225 views `TXPjVAIaKWw` — 51,4% Stay, 79,1% APV)]:
     * "Esta mamba-negra mata em segundos, mas o mangusto ri do veneno!"
     * "Trinta mil espinhos afiados contra a mordida mais letal da África."
   - [Arquétipo 2 — Escala Física / Impacto Brutal Imediato]:
     * "Setecentos quilos de músculo esmagam um leão de duzentos quilos."
     * "A trezentos metros sob o México, essa caverna de cristal ferve a 58 graus."
   - [Arquétipo 3 — O Último Erro com Sujeito Explícito (Vencedor `XoeQ1I_WHsI` — 51,3% Stay)]:
     * "O último erro de qualquer predador é desafiar as garras desse casuar."
     * "Esse búfalo de meia tonelada saiu andando, mas já está morto."
   - [Arquétipo 4 — Ameaça Direta em 2ª Pessoa com Nome da Entidade]:
     * "Se esse polvo-de-anéis-azuis tocar sua pele, você para de respirar em minutos."
3. PROIBIDO NO MERCADO BRASILEIRO:
   - NUNCA use frases vagas como "Olhe bem para..." ou "Veja o momento exato..." sem dizer o nome da espécie/fenômeno na Cena 1.
   - NUNCA divida um Short de 35 segundos em "Round 1, Round 2, Round 3" trocando para um terceiro animal que não está no título.
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
1. QUY TẮC VÀNG MỞ ĐẦU 0.0s: NÊU ĐÍCH DANH THỰC THỂ + HÌNH ẢNH VA CHẠM SỐNG ĐỘNG DỄ TƯỞNG TƯỢNG / NGHỊCH LÝ SINH TỬ NGAY Ở CẢNH 1:
   - TIÊU ĐỀ (`title`): Bắt buộc nêu đích danh thực thể + hành động/hệ quả trực quan dễ hình dung (`[Thực thể A] vs [Thực thể B]: [Cú sốc trực quan]`). CẤM nhồi đơn vị vật lý khô khan như PSI, Newton, kPa!
   - CÂU HOOK CẢNH 1 (6–10 từ): Bắt buộc nêu đích danh Thực thể chính (hoặc cặp đấu A vs B) + hình ảnh so sánh đời thường sống động hoặc nghịch lý sinh tử ngay ở giây 0.0s! TUYỆT ĐỐI CẤM dùng câu chỉ trỏ mơ hồ giấu tên chủ thể kiểu "Hãy nhìn kỹ vào..." và CẤM dùng số liệu khoa học khô khan khó mường tượng.{bench_block}
═══════════════════════════════════════════════════════════════════
"""
    else:
        return f"""
═══════════════════════════════════════════════════════════════════
🇺🇸 REGIONAL HOOK MASTERY — US / GLOBAL ENGLISH MARKET (en-US | TARGET: STAYED-TO-WATCH >= 70%)
═══════════════════════════════════════════════════════════════════
1. GOLDEN 0.0s RETENTION RULE: EXPLICIT ENTITY + VISCERAL EVERYDAY IMAGE OR LETHAL PARADOX IN SCENE 1 (6 TO 10 WORDS | GRADE-5 ENGLISH):
   - TITLE (`title`): MUST explicitly name the high-search-volume primary entities + visceral, instantly imaginable physical clash or stakes (`[Entity A] vs [Entity B]: [Visceral Stakes]`, e.g., "Jaguar vs Black Caiman: The Skull-Piercing Ambush 🐆🐊", "Komodo Dragon vs Water Buffalo: One Bite Seals Its Fate 🦎🐃"). STRICTLY BAN dry textbook physics units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`) that ordinary viewers cannot picture!
   - SCENE 1 HOOK (0.0s–2.2s, 6–10 words): Viewers scrolling the Shorts Feed DO NOT read the bottom title before hearing the first 2 seconds of voiceover! Therefore, Scene 1 MUST explicitly name the primary entity/combatants AND paint an immediate, visceral physical action or lethal paradox right at 0.0s—NEVER dry science units!
   - STRICTLY FORBIDDEN VAGUE POINTERS: NEVER start Scene 1 with vague entity-blind pointers like `"Look closely at why you cannot find the exit"`, `"Look closely at these deadly floating blue tentacles"`, or `"Watch the exact moment this massive predator feels pain"`! Real YouTube Analytics proves that hiding the subject behind `"Look closely at..."` causes 70%–94% Swipe-Away in the first 2 seconds!
2. THE 4 PROVEN HIGH-RETENTION US HOOK ARCHETYPES (CHOOSE THE #1 BEST FIT FOR SCENE 1 IN 6–10 WORDS):
   - [Archetype 1 — Direct Scale / Extreme Metric Clash (Proven Winner `A-oNKdpL0rM` 1,146 views & `w7iUy-8hjVo` 47.7% Stay)]:
     * "Fifteen hundred pounds of muscle crushes a four-hundred-pound lion."
     * "Thirty thousand razor quills versus the deadliest jaws in Africa."
     * "A fifteen-hundred-pound polar bear charges a six-hundred-pound Siberian tiger."
   - [Archetype 2 — Walking Dead / Lethal Paradox with Named Entity (Proven Winner `1NeB0Qe6_t4` 1,325 views in 12h & `D_sIws9Buto` 123.8% APV)]:
     * "This twelve-hundred-pound buffalo walked away from a Komodo dragon, already dead."
     * "Earth survived only because the Krakatoa volcano killed thousands."
     * "The Jaguar doesn't hunt like lions—it targets skull kill-switches."
   - [Archetype 3 — Concrete Historical / Scientific Shock Event (Proven Winner `PVoVipDbLt8` 87.5% APV)]:
     * "One hundred thousand Mongol horsemen vanished forty miles from Vienna."
     * "In 1815, Mount Tambora's eruption buried June under freezing snowstorms."
   - [Archetype 4 — Named Psychological / Behavioral Trap]:
     * "Las Vegas casinos remove every clock to trap your brain's dopamine."
     * "FBI interrogators place one water cup six inches from guilty suspects."
3. STRICTLY BANNED IN US SHORTS:
   - NO vague `"Look closely at..."` or `"Watch the exact moment..."` hooks that hide the primary subject in Scene 1.
   - NO multi-round gauntlets (`"Round One... Round Two... Round Three..."`) or bait-and-switch 3rd animals inside a 35-second Short: all 7–8 scenes MUST stay 100% focused on the exact entities promised in the `title`!
   - NO abstract poetry ("loaded weapon", "silent cage"), and NO passive Yes/No questions ("Did you know...?").
4. 1-1 VISUAL CONTRAST & LEAD-SUBJECT ALTERNATION RULE (BREAKING THE ~1,000-VIEW SEED COHORT CEILING):
   - Real YouTube Analytics v2 telemetry proves that starting consecutive uploads with the exact same lead subject/species noun or identical opening visual color palette triggers Audience Cohort Fatigue (Stayed-to-Watch drops from ~48.5% to ~32.8%, capping views at ~1,000).
   - NEVER start the new `title` or Scene 1 visual hook with the same primary subject noun/entity that led the channel's last 2-3 videos! Always alternate visual palettes and foreground the freshest, most visually contrasting phenomenon or opponent.
5. ORGANIC 50/50 COUNTERFACTUAL DEBATE COMMENT (`pinned_comment` — ZERO 'SUBSCRIBE' SPAM):
   - NEVER ask a question in `pinned_comment` that the video already answered (e.g. if the video already showed why the Mongoose beats the Black Mamba, DO NOT ask "Who wins: Mongoose or Black Mamba?").
   - NEVER append robotic promotional footers like "🔔 SUBSCRIBE for more episodes!".
   - Write a 1-sentence Counterfactual Twist / Escalated Odds / Next-Opponent Dilemma (12 to 22 words) ending with `(1) ... or (2) ... 👇` that splits the comments 50/50 (e.g. "Be honest: if it were 2 Black Mambas at the same time, does the Mongoose still survive? (1) Yes or (2) No chance? 👇").{bench_block}
═══════════════════════════════════════════════════════════════════
"""
