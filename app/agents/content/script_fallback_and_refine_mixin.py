"""app.agents.content.script_fallback_and_refine_mixin — Kịch bản dự phòng 5 hồi, tinh chỉnh kịch bản & hội thoại Brainstorm."""
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



from app.agents.content.prompt_instructions import (
    build_language_integrity_instruction,
    build_regional_hook_mastery_instruction,
)


class ScriptFallbackAndRefineMixin:
    def _build_curated_5act_script_fallback(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        video_format: str = "short",
    ) -> ScriptContract:
        """
        Bộ khung kịch bản 5 Hồi (8 phân cảnh, 95-115 từ) chuẩn Viral Shorts dự phòng
        khi toàn bộ Gemini API Keys tạm chạm hạn mức 429.
        Đảm bảo 100% đúng ngôn ngữ (pt-BR / en), đúng ngách (Biển sâu vs Trên cạn),
        và có từ khóa hình ảnh tiếng Anh cụ thể cho từng cảnh.
        """
        t_low = (topic or "").lower()
        n_low = (channel_niche or "").lower()
        is_pt = str(language or "").lower().startswith("pt")

        if is_pt and ("deep_sea" in n_low or "mystery" in n_low or "oceano" in t_low or "lula" in t_low or "nemo" in t_low or "tubarão" in t_low or "fossa" in t_low or "mergulhador" in t_low):
            if "nemo" in t_low or "cemitério" in t_low or "nave" in t_low or "sinal" in t_low:
                raw_data = {
                    "title": "Ponto Nemo: O Cemitério Secreto De Naves No Fundo Do Oceano 🌊🛰️ #Shorts",
                    "topic": topic,
                    "primary_entity": "Point Nemo",
                    "hook": "260 naves espaciais afundaram neste cemitério secreto do Pacífico!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["deep ocean abyss underwater", "spacecraft reentry ocean splash"],
                    "thumbnail_text": "CEMITÉRIO NO OCEANO! 🌊",
                    "pinned_comment": "Você teria coragem de navegar pelo Ponto Nemo à noite no meio do oceano? Comente! 👇",
                    "tags": ["shorts", "oceano", "misteriosdomundo", "pontonemo", "abismo"],
                    "scenes": [
                        {"scene_id": 1, "narration": "260 naves espaciais afundaram neste cemitério secreto do Pacífico!", "visual_keyword": "deep pacific ocean dark waves", "stock_query": "deep ocean waves", "visual_prompt": "Dark stormy Pacific Ocean waters from above", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Esse lugar se chama Ponto Nemo, o local mais isolado de todos os oceanos da Terra.", "visual_keyword": "remote ocean horizon aerial view", "stock_query": "remote ocean aerial", "visual_prompt": "Endless deep blue ocean far from any continent", "estimated_seconds": 4.5},
                        {"scene_id": 3, "narration": "Ele fica a 2.688 quilômetros da costa mais próxima, cercado apenas por águas profundas.", "visual_keyword": "underwater deep blue sea abyss", "stock_query": "underwater deep sea", "visual_prompt": "Deep underwater ocean rays fading into darkness", "estimated_seconds": 4.5},
                        {"scene_id": 4, "narration": "Quando um navio passa por lá, os humanos mais próximos estão na Estação Espacial Internacional.", "visual_keyword": "international space station orbit earth", "stock_query": "space station earth", "visual_prompt": "International Space Station orbiting above the Pacific Ocean", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Por isso as agências espaciais usam essa fossa oceânica para afundar satélites gigantes desativados.", "visual_keyword": "satellite burning atmosphere ocean", "stock_query": "satellite space orbit", "visual_prompt": "Spacecraft debris falling toward the ocean", "estimated_seconds": 4.8},
                        {"scene_id": 6, "narration": "Até a antiga estação soviética Mir de 120 toneladas repousa despedaçada no leito marinho escuro.", "visual_keyword": "sunken shipwreck deep ocean floor", "stock_query": "deep ocean shipwreck", "visual_prompt": "Metallic wreckage resting on the pitch black ocean floor", "estimated_seconds": 4.8},
                        {"scene_id": 7, "narration": "A pressão extrema e as correntes geladas bloqueiam quase toda vida marinha ao redor dos destroços.", "visual_keyword": "deep sea jellyfish bioluminescent dark", "stock_query": "deep sea creature", "visual_prompt": "Bioluminescent deep sea creature drifting in pitch black water", "estimated_seconds": 4.6},
                        {"scene_id": 8, "narration": "E é nesse silêncio absoluto do abismo marinho que o maior segredo do Pacífico permanece intocado.", "visual_keyword": "dark ocean trench underwater", "stock_query": "dark ocean underwater", "visual_prompt": "Deep ocean trench vanishing into darkness", "estimated_seconds": 4.5},
                    ],
                }
            else:
                raw_data = {
                    "title": "Lula-Colossal vs Cachalote: O Duelo Mortal A 2.000m No Oceano 🦑🐋 #Shorts",
                    "topic": topic,
                    "primary_entity": "Colossal Squid",
                    "hook": "Lula-colossal contra cachalote: o duelo mais brutal do oceano!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["sperm whale diving deep ocean", "giant squid underwater ocean"],
                    "thumbnail_text": "DUELO A 2.000M NO MAR! 🦑",
                    "pinned_comment": "Quem vence na escuridão total do oceano profundo: Cachalote ou Lula-Colossal? Comente! 👇",
                    "tags": ["shorts", "oceano", "misteriosdomundo", "baleia", "abismo"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Lula-colossal contra cachalote: o duelo mais brutal do oceano!", "visual_keyword": "sperm whale diving deep ocean", "stock_query": "sperm whale underwater", "visual_prompt": "Massive sperm whale diving into the dark ocean abyss", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Essas marcas circulares na pele da baleia vêm dos tentáculos da temida lula-colossal.", "visual_keyword": "giant squid tentacles underwater dark", "stock_query": "squid underwater ocean", "visual_prompt": "Giant squid tentacles moving through dark ocean water", "estimated_seconds": 4.5},
                        {"scene_id": 3, "narration": "Com 14 metros e olhos do tamanho de pratos, ela caça na escuridão congelante da Antártida.", "visual_keyword": "deep sea creature glowing eyes", "stock_query": "deep ocean creature", "visual_prompt": "Huge squid eye gleaming in the pitch black Southern Ocean", "estimated_seconds": 4.6},
                        {"scene_id": 4, "narration": "Diferente da lula comum, suas ventosas possuem ganchos afiados que giram 360 graus na carne.", "visual_keyword": "octopus tentacles closeup underwater", "stock_query": "tentacles underwater closeup", "visual_prompt": "Powerful cephalopod tentacles gripping in deep water", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Mas o cachalote de 50 toneladas mergulha com a maior arma acústica de todos os oceanos.", "visual_keyword": "whale swimming deep blue water", "stock_query": "whale deep ocean", "visual_prompt": "Giant whale descending into the deep blue trench", "estimated_seconds": 4.6},
                        {"scene_id": 6, "narration": "Ele dispara cliques de sonar de 230 decibéis que funcionam como uma onda de choque submarina.", "visual_keyword": "underwater sonar sound wave ocean", "stock_query": "underwater ocean rays", "visual_prompt": "Acoustic shockwave rippling through dark underwater depths", "estimated_seconds": 4.8},
                        {"scene_id": 7, "narration": "O impacto sonoro desorienta a lula-colossal no abismo antes do golpe final das mandíbulas.", "visual_keyword": "predator whale hunting deep sea", "stock_query": "whale hunting ocean", "visual_prompt": "Sperm whale emerging victorious from the deep sea", "estimated_seconds": 4.6},
                        {"scene_id": 8, "narration": "Ainda assim, nas fossas mais profundas do oceano, essa guerra invisível acontece todos os dias.", "visual_keyword": "dark ocean abyssal trench", "stock_query": "dark ocean abyss", "visual_prompt": "Endless dark ocean abyss at 2000 meters depth", "estimated_seconds": 4.4},
                    ],
                }
        elif is_pt:
            if "ariranha" in t_low or "jacaré-açu" in t_low or "bando" in t_low:
                raw_data = {
                    "title": "Por Que Até o Jacaré-Açu Foge de um Bando de Ariranhas 🦦🐊 #Shorts",
                    "topic": topic,
                    "primary_entity": "Giant Otter",
                    "hook": "Até o jacaré-açu foge quando as ariranhas cercam o rio!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["giant river otter amazon", "black caiman amazon river"],
                    "thumbnail_text": "TERROR DOS RIOS! 🦦",
                    "pinned_comment": "Você sabia que as ariranhas são chamadas de lobos dos rios na Amazônia? Comente! 👇",
                    "tags": ["shorts", "animais", "natureza", "amazonia", "pantanal", "predadores"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Até o jacaré-açu foge quando as ariranhas cercam o rio!", "visual_keyword": "giant river otter swimming amazon", "stock_query": "river otter swimming", "visual_prompt": "Pack of giant river otters patrolling the Amazon river", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Sozinha, uma ariranha de quase dois metros já possui mandíbulas capazes de triturar ossos duros.", "visual_keyword": "otter teeth closeup wild", "stock_query": "otter closeup", "visual_prompt": "Giant otter baring sharp teeth at the water surface", "estimated_seconds": 4.6},
                        {"scene_id": 3, "narration": "Mas o verdadeiro perigo na Amazônia é que esses predadores jamais caçam sozinhos.", "visual_keyword": "group of otters river bank", "stock_query": "otters river", "visual_prompt": "Coordinated family group of giant otters in tropical river", "estimated_seconds": 4.4},
                        {"scene_id": 4, "narration": "Um bando coordenado de até dez ariranhas age exatamente como uma alcateia de lobos dentro d'água.", "visual_keyword": "otters swimming underwater fast", "stock_query": "otter underwater", "visual_prompt": "Sleek otters darting underwater at high speed", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Quando um jacaré invade o território, o grupo inteiro ataca pelas laterais e pela cauda ao mesmo tempo.", "visual_keyword": "caiman crocodile river water", "stock_query": "caiman river", "visual_prompt": "Large caiman surrounded in muddy Amazon river water", "estimated_seconds": 4.9},
                        {"scene_id": 6, "narration": "Sem conseguir girar rápido o suficiente para morder, o réptil gigante perde fôlego em poucos minutos.", "visual_keyword": "crocodile splashing water river", "stock_query": "crocodile water", "visual_prompt": "Caiman thrashing defensively in shallow river", "estimated_seconds": 4.7},
                        {"scene_id": 7, "narration": "Mesmo a própria onça-pintada evita entrar na água quando ouve os gritos de alerta do bando.", "visual_keyword": "jaguar looking at river", "stock_query": "jaguar river", "visual_prompt": "Jaguar watching cautiously from the riverbank", "estimated_seconds": 4.6},
                        {"scene_id": 8, "narration": "É por essa união implacável que até o jacaré-açu recua quando as ariranhas dominam o rio.", "visual_keyword": "amazon river aerial jungle", "stock_query": "amazon river jungle", "visual_prompt": "Dark winding Amazon river surrounded by dense rainforest", "estimated_seconds": 4.4},
                    ],
                }
            else:
                raw_data = {
                    "title": "Onça-Pintada vs Jacaré: A Mordida Que Fura o Crânio no Pantanal 🐆🐊 #Shorts",
                    "topic": topic,
                    "primary_entity": "Jaguar",
                    "hook": "A onça-pintada mata um jacaré com uma única mordida no crânio!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["jaguar hunting caiman pantanal", "jaguar swimming river wild"],
                    "thumbnail_text": "FURA O CRÂNIO!",
                    "pinned_comment": "Honestamente: se fossem dois jacarés adultos na água funda, a onça ainda venceria? (1) Sim ou (2) Sem chance? 👇",
                    "tags": ["shorts", "animais", "oncapintada", "pantanal", "natureza", "predadores"],
                    "scenes": [
                        {"scene_id": 1, "narration": "A onça-pintada mata um jacaré com uma única mordida no crânio!", "visual_keyword": "jaguar hunting caiman river", "stock_query": "jaguar river", "visual_prompt": "Powerful jaguar leaping into Pantanal river water", "estimated_seconds": 4.2},
                        {"scene_id": 2, "narration": "Enquanto leões e tigres atacam a garganta para sufocar a presa, o maior felino das Américas usa outra tática.", "visual_keyword": "jaguar walking jungle closeup", "stock_query": "jaguar walking", "visual_prompt": "Muscular jaguar stalking silently along a muddy riverbank", "estimated_seconds": 4.8},
                        {"scene_id": 3, "narration": "Sua mandíbula compacta funciona como um alicate hidráulico, capaz de furar ossos duros como se fossem uma lata vazia.", "visual_keyword": "jaguar roaring sharp teeth", "stock_query": "jaguar closeup", "visual_prompt": "Closeup of jaguar jaws and massive canine teeth", "estimated_seconds": 4.7},
                        {"scene_id": 4, "narration": "No Pantanal, ela nada em silêncio absoluto sem criar uma única ondulação na superfície da água.", "visual_keyword": "jaguar swimming water stealth", "stock_query": "jaguar swimming", "visual_prompt": "Jaguar swimming stealthily through dark river water", "estimated_seconds": 4.6},
                        {"scene_id": 5, "narration": "Antes que o jacaré perceba a sombra, a onça salta do barranco direto sobre o dorso blindado do réptil.", "visual_keyword": "caiman resting river bank", "stock_query": "caiman crocodile", "visual_prompt": "Caiman basking on a sandy riverbank in the Pantanal", "estimated_seconds": 4.8},
                        {"scene_id": 6, "narration": "Em vez de lutar contra a couraça, ela crava os caninos exatamente entre as orelhas e perfura o cérebro instantaneamente.", "visual_keyword": "jaguar biting prey river", "stock_query": "big cat hunting", "visual_prompt": "Jaguar clamping down with lethal precision in shallow water", "estimated_seconds": 5.0},
                        {"scene_id": 7, "narration": "O predador aquático de oitenta quilos fica paralisado no mesmo segundo, sem conseguir dar um único golpe de cauda.", "visual_keyword": "jaguar carrying prey riverbank", "stock_query": "jaguar nature", "visual_prompt": "Jaguar dragging its prize up the steep muddy bank", "estimated_seconds": 4.8},
                        {"scene_id": 8, "narration": "É por essa força cirúrgica que a onça-pintada reina absoluta nas águas e nas matas do Pantanal.", "visual_keyword": "jaguar resting tree branch", "stock_query": "jaguar forest", "visual_prompt": "Apex jaguar surveying the Pantanal wetlands from a tree", "estimated_seconds": 4.5},
                    ],
                }
        elif "history" in n_low or "what_if" in n_low:
            if "petrov" in t_low or "1983" in t_low or "satellite" in t_low or "false alarm" in t_low:
                raw_data = {
                    "title": "1983 Stanislav Petrov vs Soviet Nuclear False Alarm 🚨☢️ #Shorts",
                    "topic": topic,
                    "primary_entity": "Stanislav Petrov",
                    "hook": "Five nuclear missiles appeared on screen and one man refused to fire back!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["missile launch military radar", "radar screen control room dark"],
                    "thumbnail_text": "15 MINS TO DOOMSDAY! 🚨",
                    "pinned_comment": "If Stanislav Petrov had followed Soviet protocol that night in 1983, would any major city still be standing today? 👇",
                    "tags": ["shorts", "alternatehistory", "whatif", "history", "coldwar"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Five nuclear missiles appeared on screen and one man refused to fire back!", "visual_keyword": "military radar screen red alarm", "stock_query": "radar screen military", "visual_prompt": "Glowing Soviet early warning radar console flashing red alerts", "estimated_seconds": 4.4},
                        {"scene_id": 2, "narration": "Just after midnight in September 1983, the Soviet Oko satellite bunker outside Moscow erupted with sirens.", "visual_keyword": "military bunker corridor red light", "stock_query": "dark corridor red light", "visual_prompt": "Underground command bunker illuminated by flashing emergency lights", "estimated_seconds": 4.8},
                        {"scene_id": 3, "narration": "The computer reported with highest confidence that America had launched five intercontinental ballistic missiles.", "visual_keyword": "rocket missile launch night sky", "stock_query": "rocket launch night", "visual_prompt": "Ballistic missile streaking into the dark night sky", "estimated_seconds": 4.8},
                        {"scene_id": 4, "narration": "Strict Soviet doctrine gave duty officer Stanislav Petrov less than fifteen minutes to trigger full retaliation.", "visual_keyword": "military officer staring computer screen", "stock_query": "man staring computer dark", "visual_prompt": "Tense officer sweating in front of glowing control room monitors", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "In one hand he held the direct phone line to the Kremlin leadership waiting for his confirmation.", "visual_keyword": "vintage telephone desk dark room", "stock_query": "old telephone dark", "visual_prompt": "Heavy command telephone sitting on a steel control desk", "estimated_seconds": 4.5},
                        {"scene_id": 6, "narration": "But Petrov noticed a crucial flaw: ground radar showed nothing, and nobody starts World War Three with only five missiles.", "visual_keyword": "satellite orbiting earth clouds sun", "stock_query": "satellite orbit earth", "visual_prompt": "Orbital satellite passing high above sunlit clouds in space", "estimated_seconds": 5.2},
                        {"scene_id": 7, "narration": "Trusting his gut over a billion-dollar computer, he declared a false alarm caused by sunlight reflecting off high clouds.", "visual_keyword": "sunrise clouds aerial view sky", "stock_query": "sunlight clouds aerial", "visual_prompt": "Blinding sunlight reflecting off high altitude clouds above Earth", "estimated_seconds": 5.0},
                        {"scene_id": 8, "narration": "Minutes later zero warheads hit, proving Petrov's fifteen-minute gamble saved humanity from total annihilation.", "visual_keyword": "city skyline night lights peaceful", "stock_query": "city skyline night", "visual_prompt": "Peaceful city skyline glowing at night under a clear sky", "estimated_seconds": 4.7},
                    ],
                }
            else:
                raw_data = {
                    "title": "1962 Vasili Arkhipov vs B-59 Nuclear Torpedo ☢️🚢 #Shorts",
                    "topic": topic,
                    "primary_entity": "Vasili Arkhipov",
                    "hook": "One Soviet officer refused to launch World War Three!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["submarine underwater ocean", "naval warship sea storm"],
                    "thumbnail_text": "1 VOTE SAVED EARTH! ☢️",
                    "pinned_comment": "If Vasili Arkhipov had said YES on submarine B-59 that day, would civilization still exist? 👇",
                    "tags": ["shorts", "alternatehistory", "whatif", "history", "coldwar"],
                    "scenes": [
                        {"scene_id": 1, "narration": "One Soviet officer refused to launch World War Three!", "visual_keyword": "submarine underwater dark ocean", "stock_query": "submarine underwater", "visual_prompt": "Soviet diesel submarine submerged in dark Atlantic waters", "estimated_seconds": 4.3},
                        {"scene_id": 2, "narration": "American destroyers had cornered Soviet submarine B-59 near Cuba and begun dropping signaling depth charges.", "visual_keyword": "navy destroyer warship ocean waves", "stock_query": "navy warship ocean", "visual_prompt": "US Navy destroyers cutting through rough ocean waves", "estimated_seconds": 4.8},
                        {"scene_id": 3, "narration": "Cut off from Moscow for days in 120-degree heat, the exhausted crew believed World War Three had started.", "visual_keyword": "submarine control room gauges red light", "stock_query": "submarine interior red", "visual_prompt": "Tense submarine control room bathed in red emergency light", "estimated_seconds": 4.8},
                        {"scene_id": 4, "narration": "Inside the hull sat a 10-kiloton nuclear torpedo capable of vaporizing the entire American carrier fleet.", "visual_keyword": "torpedo tube submarine weapon", "stock_query": "military torpedo", "visual_prompt": "Heavy torpedo loaded inside a submarine launch tube", "estimated_seconds": 4.6},
                        {"scene_id": 5, "narration": "Captain Savitsky and the political officer both signed off to launch the nuclear strike immediately.", "visual_keyword": "military officer radar screen", "stock_query": "radar screen military", "visual_prompt": "Naval officers staring at sonar and radar consoles", "estimated_seconds": 4.5},
                        {"scene_id": 6, "narration": "What if their order had been enough? Washington and Moscow would have been glassed within ninety minutes.", "visual_keyword": "nuclear explosion mushroom cloud", "stock_query": "explosion cloud", "visual_prompt": "Massive nuclear mushroom cloud rising over the horizon", "estimated_seconds": 4.8},
                        {"scene_id": 7, "narration": "Instead, flotilla commander Vasili Arkhipov stood his ground in the stifling control room and vetoed the launch.", "visual_keyword": "naval commander portrait dramatic light", "stock_query": "navy captain", "visual_prompt": "Determined Soviet naval officer refusing the launch key", "estimated_seconds": 4.8},
                        {"scene_id": 8, "narration": "That single stubborn refusal forced B-59 to surface and stopped the deadliest torpedo in history.", "visual_keyword": "submarine surfacing ocean sunset", "stock_query": "submarine ocean", "visual_prompt": "Submarine surfacing peacefully on the ocean waves", "estimated_seconds": 4.5},
                    ],
                }
        elif "psychology" in n_low:
            if "franklin" in t_low or "favor" in t_low or "rival" in t_low or "enemy" in t_low:
                raw_data = {
                    "title": "Benjamin Franklin Effect vs Rivals: The 2-Minute Favor 🧠🤝 #Shorts",
                    "topic": topic,
                    "primary_entity": "Benjamin Franklin Effect",
                    "hook": "Want a rival to secretly like you? Ask them for one tiny favor!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["two businessmen talking office dark", "chess game move closeup"],
                    "thumbnail_text": "2-MINUTE FAVOR TRICK! 🧠",
                    "pinned_comment": "Have you ever asked someone who disliked you for a small favor and watched their attitude flip? 👇",
                    "tags": ["shorts", "psychology", "darkpsychology", "benjaminfranklin", "humanbehavior"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Want a rival to secretly like you? Ask them for one tiny favor!", "visual_keyword": "two businessmen handshake dark office", "stock_query": "businessmen talking dark", "visual_prompt": "Two sharp-suited rivals facing each other across a dimly lit office", "estimated_seconds": 4.3},
                        {"scene_id": 2, "narration": "Most people try to win over enemies by doing favors for them, which actually makes rivals feel superior.", "visual_keyword": "man looking down arrogant suit", "stock_query": "businessman serious portrait", "visual_prompt": "Cold executive looking down with a skeptical expression", "estimated_seconds": 4.8},
                        {"scene_id": 3, "narration": "Benjamin Franklin discovered the exact opposite trick when a powerful politician refused to speak to him.", "visual_keyword": "vintage library books candle light", "stock_query": "old library books", "visual_prompt": "Rare leather-bound book sitting on a dark mahogany desk", "estimated_seconds": 4.7},
                        {"scene_id": 4, "narration": "Instead of flattering the man, Franklin wrote a polite note asking to borrow one rare book for two days.", "visual_keyword": "hand writing letter fountain pen", "stock_query": "writing letter pen", "visual_prompt": "Close shot of a hand writing a note with a classic pen", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "The rival sent the book immediately, and the next time they met, he shook Franklin's hand like an old friend.", "visual_keyword": "men shaking hands office meeting", "stock_query": "business handshake", "visual_prompt": "Warm handshake between two executives in a dark boardroom", "estimated_seconds": 4.9},
                        {"scene_id": 6, "narration": "Psychologists call this cognitive dissonance: your brain hates doing favors for someone it claims to despise.", "visual_keyword": "human brain neural pathways glowing", "stock_query": "brain neural network", "visual_prompt": "Glowing neural synapses rewiring inside the human brain", "estimated_seconds": 4.9},
                        {"scene_id": 7, "narration": "To resolve that mental glitch, their subconscious forces them to decide you must actually be worth liking.", "visual_keyword": "closeup human eye pupil dilating", "stock_query": "closeup human eye", "visual_prompt": "Extreme macro shot of a human eye shifting focus in low light", "estimated_seconds": 4.8},
                        {"scene_id": 8, "narration": "That is why asking a cold rival for a two-minute favor rewires their brain faster than any compliment.", "visual_keyword": "chess king piece checkmate dark", "stock_query": "chess game dark", "visual_prompt": "Hand moving a king piece across a dark chessboard", "estimated_seconds": 4.7},
                    ],
                }
            else:
                raw_data = {
                    "title": "Mirror Neuron Yawn vs Psychopaths: The 3-Second Empathy Test 🧠👁️ #Shorts",
                    "topic": topic,
                    "primary_entity": "Mirror Neurons",
                    "hook": "Psychopaths fail this simple three-second yawn test every time!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["man cold stare dark shadow", "detective interrogation room dark"],
                    "thumbnail_text": "3-SECOND YAWN TEST! 🥶",
                    "pinned_comment": "Have you ever tested the contagious yawn reflex on someone watching you in a room? 👇",
                    "tags": ["shorts", "psychology", "darkpsychology", "bodylanguage", "humanbehavior"],
                    "scenes": [
                        {"scene_id": 1, "narration": "Psychopaths fail this simple three-second yawn test every time!", "visual_keyword": "man cold expression shadow portrait", "stock_query": "man dark shadow portrait", "visual_prompt": "Cold unblinking man staring across a dimly lit noir room", "estimated_seconds": 4.1},
                        {"scene_id": 2, "narration": "When a normal person sees someone yawn, their brain automatically triggers a contagious yawn within seconds.", "visual_keyword": "two people conversation cafe dark", "stock_query": "people talking dark room", "visual_prompt": "Two people sitting across a table in a moody room", "estimated_seconds": 4.5},
                        {"scene_id": 3, "narration": "Neuroscientists proved this reflex has nothing to do with being tired—it is wired directly to your mirror neurons.", "visual_keyword": "human brain neural network glowing", "stock_query": "brain neural network", "visual_prompt": "Glowing mirror neuron pathways firing inside the human brain", "estimated_seconds": 4.7},
                        {"scene_id": 4, "narration": "Those exact mirror neurons force your brain to unconsciously feel what the person in front of you feels.", "visual_keyword": "extreme closeup human eye reflection", "stock_query": "closeup eye dark", "visual_prompt": "Sharp closeup of an intense human eye reflecting light", "estimated_seconds": 4.5},
                        {"scene_id": 5, "narration": "In a Baylor University forensic study, people scoring highest on cold-hearted psychopathy were almost completely immune.", "visual_keyword": "detective reviewing forensic files dark", "stock_query": "detective case files", "visual_prompt": "Forensic psychologist examining behavioral test charts in shadow", "estimated_seconds": 4.8},
                        {"scene_id": 6, "narration": "They can fake a charming smile on command, but they cannot fake an involuntary empathy reflex.", "visual_keyword": "man adjusting suit mirror shadow", "stock_query": "man mirror dark", "visual_prompt": "Man in a sharp suit looking into a dark mirror with a cold expression", "estimated_seconds": 4.5},
                        {"scene_id": 7, "narration": "Next time you feel someone secretly staring at you, fake one slow yawn and watch their face for three seconds.", "visual_keyword": "person watching from shadow doorway", "stock_query": "silhouette doorway shadow", "visual_prompt": "Observer watching a suspect's face across a quiet room", "estimated_seconds": 4.7},
                        {"scene_id": 8, "narration": "Normal people mirror your face without thinking, which is why psychopaths fail this simple three-second yawn test every time.", "visual_keyword": "man cold stare camera noir", "stock_query": "intense stare portrait", "visual_prompt": "Chilling chiaroscuro portrait of an unmoved face in darkness", "estimated_seconds": 4.6},
                    ],
                }
        elif "deep_sea" in n_low or "mystery" in n_low:
            raw_data = {
                "title": "The Secret Spacecraft Graveyard At The Bottom Of The Ocean 🌊🛰️ #Shorts",
                "topic": topic,
                "primary_entity": "Point Nemo",
                "hook": "260 sunken spacecraft rest in this secret ocean graveyard!",
                "hook_visual_mode": "video",
                "hook_search_queries": ["deep ocean underwater abyss", "satellite space orbit earth"],
                "thumbnail_text": "OCEAN SPACE GRAVEYARD! 🌊",
                "pinned_comment": "Would you dare sail across Point Nemo at night knowing you are 1,670 miles from land? 👇",
                "tags": ["shorts", "deepsea", "oceanmysteries", "pointnemo", "science"],
                "scenes": [
                    {"scene_id": 1, "narration": "260 sunken spacecraft rest in this secret ocean graveyard!", "visual_keyword": "deep pacific ocean dark waves", "stock_query": "deep ocean waves", "visual_prompt": "Dark swells of the South Pacific Ocean", "estimated_seconds": 4.3},
                    {"scene_id": 2, "narration": "Scientists call this coordinate Point Nemo, the oceanic pole of inaccessibility.", "visual_keyword": "remote ocean horizon aerial view", "stock_query": "remote ocean aerial", "visual_prompt": "Aerial view of endless deep blue ocean water", "estimated_seconds": 4.4},
                    {"scene_id": 3, "narration": "It sits 1,670 miles from the nearest strip of land in every single direction.", "visual_keyword": "underwater deep blue sea abyss", "stock_query": "underwater deep sea", "visual_prompt": "Sunlight fading into the deep ocean abyss", "estimated_seconds": 4.4},
                    {"scene_id": 4, "narration": "When you stand at Point Nemo, the closest human beings to you are astronauts orbiting 250 miles overhead.", "visual_keyword": "international space station orbit earth", "stock_query": "space station earth", "visual_prompt": "International Space Station orbiting above Earth's oceans", "estimated_seconds": 4.9},
                    {"scene_id": 5, "narration": "Because no ships sail there, space agencies deliberately crash dying satellites straight into this abyssal trench.", "visual_keyword": "satellite burning atmosphere ocean", "stock_query": "satellite space orbit", "visual_prompt": "Fiery spacecraft debris streaking over the ocean", "estimated_seconds": 4.8},
                    {"scene_id": 6, "narration": "Even the 120-ton Russian Mir space station rests shattered across the freezing seafloor.", "visual_keyword": "sunken shipwreck deep ocean floor", "stock_query": "deep ocean shipwreck", "visual_prompt": "Sunken metallic wreckage on the dark ocean floor", "estimated_seconds": 4.6},
                    {"scene_id": 7, "narration": "Crushing pressure and nutrient-starved currents leave the surrounding water almost completely devoid of fish.", "visual_keyword": "deep sea jellyfish bioluminescent dark", "stock_query": "deep sea creature", "visual_prompt": "Lonely bioluminescent jellyfish drifting in pitch black water", "estimated_seconds": 4.7},
                    {"scene_id": 8, "narration": "And in that pitch-black oceanic silence, humanity's largest space machines slowly rust in the abyss.", "visual_keyword": "dark ocean trench underwater", "stock_query": "dark ocean underwater", "visual_prompt": "Pitch black abyssal seafloor at 13,000 feet", "estimated_seconds": 4.5},
                ],
            }
        else:
            if "honey badger" in t_low or "cobra" in t_low or "snake" in t_low or "mamba" in t_low:
                raw_data = {
                    "title": "Honey Badger vs King Cobra: How It Sleeps Off Lethal Venom 🦡🐍 #Shorts",
                    "topic": topic,
                    "primary_entity": "Honey Badger",
                    "hook": "A honey badger gets bitten by a cobra and just takes a nap!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["honey badger fighting snake", "king cobra hood strike"],
                    "thumbnail_text": "IMMUNE TO VENOM! 🦡",
                    "pinned_comment": "Is the Honey Badger pound-for-pound the most fearless animal on Earth? Drop your vote 👇",
                    "tags": ["shorts", "wildlife", "honeybadger", "kingcobra", "predators", "nature"],
                    "scenes": [
                        {"scene_id": 1, "narration": "A honey badger gets bitten by a cobra and just takes a nap!", "visual_keyword": "honey badger walking savanna wild", "stock_query": "badger wildlife", "visual_prompt": "Fearless honey badger stalking across dry African brush", "estimated_seconds": 4.3},
                        {"scene_id": 2, "narration": "One bite from a Cape cobra pumps enough neurotoxin to paralyze an adult lion within minutes.", "visual_keyword": "cobra snake hood aggressive wild", "stock_query": "cobra snake", "visual_prompt": "Venomous cobra rearing up with hood flared", "estimated_seconds": 4.6},
                        {"scene_id": 3, "narration": "Yet the thirty-pound honey badger actively hunts venomous snakes as its favorite afternoon meal.", "visual_keyword": "badger hunting snake desert", "stock_query": "snake hunting", "visual_prompt": "Honey badger charging straight at a coiled venomous snake", "estimated_seconds": 4.7},
                        {"scene_id": 4, "narration": "Its first defense is quarter-inch-thick rubbery skin so loose it can twist around and bite back.", "visual_keyword": "badger claws fur closeup", "stock_query": "wild animal closeup", "visual_prompt": "Thick loose hide and razor claws of a honey badger", "estimated_seconds": 4.8},
                        {"scene_id": 5, "narration": "Even when the cobra's fangs pierce its face, a rare genetic mutation saves the badger's life.", "visual_keyword": "snake fangs venom strike", "stock_query": "snake strike", "visual_prompt": "Close shot of venomous snake striking forward", "estimated_seconds": 4.6},
                        {"scene_id": 6, "narration": "Modified nicotinic receptors in its nervous system physically block alpha-neurotoxins from binding to its muscles.", "visual_keyword": "neural network glowing biology", "stock_query": "nervous system", "visual_prompt": "Molecular shield blocking venom molecules inside nerve cells", "estimated_seconds": 5.0},
                        {"scene_id": 7, "narration": "After crushing the snake's skull, the badger passes out for two hours while its liver flushes the venom.", "visual_keyword": "mammal sleeping ground savanna", "stock_query": "animal sleeping wild", "visual_prompt": "Honey badger resting in the shade on dry earth", "estimated_seconds": 4.9},
                        {"scene_id": 8, "narration": "Then it wakes up completely unharmed and finishes eating the cobra before trotting back into the bush.", "visual_keyword": "honey badger running wild nature", "stock_query": "badger nature", "visual_prompt": "Honey badger trotting fearlessly across the African savanna", "estimated_seconds": 4.6},
                    ],
                }
            else:
                raw_data = {
                    "title": "Why Caimans Are Terrified Of Jaguars In Deep Water 🐆🐊 #Shorts",
                    "topic": topic,
                    "primary_entity": "Jaguar",
                    "hook": "One jaguar bite crushes a caiman's skull straight through bone!",
                    "hook_visual_mode": "video",
                    "hook_search_queries": ["jaguar hunting caiman river", "jaguar swimming pantanal"],
                    "thumbnail_text": "CRUSHES SKULLS!",
                    "pinned_comment": "Be honest: if two adult caimans attacked in deep water, could the Jaguar still win? (1) Yes or (2) No chance? 👇",
                    "tags": ["shorts", "wildlife", "jaguar", "predators", "bigcats", "nature"],
                    "scenes": [
                        {"scene_id": 1, "narration": "One jaguar bite crushes a caiman's skull straight through bone!", "visual_keyword": "jaguar hunting caiman river", "stock_query": "jaguar river", "visual_prompt": "Powerful jaguar leaping into a river to ambush a caiman", "estimated_seconds": 4.3},
                        {"scene_id": 2, "narration": "In the flooded jungles of the Pantanal, eight-foot caimans should be the undisputed rulers of the water.", "visual_keyword": "caiman crocodile swimming river", "stock_query": "caiman river", "visual_prompt": "Large caiman floating silently in dark river water", "estimated_seconds": 4.7},
                        {"scene_id": 3, "narration": "Instead, they flee onto muddy banks the moment a jaguar steps into the river.", "visual_keyword": "jaguar walking riverbank jungle", "stock_query": "jaguar walking", "visual_prompt": "Muscular jaguar prowling along a tropical riverbank", "estimated_seconds": 4.5},
                        {"scene_id": 4, "narration": "Unlike lions or tigers that choke prey by the throat, the jaguar evolved a stocky jaw built like bolt cutters.", "visual_keyword": "jaguar roaring fangs closeup", "stock_query": "jaguar closeup", "visual_prompt": "Extreme closeup of a jaguar's massive jaws and canine teeth", "estimated_seconds": 5.0},
                        {"scene_id": 5, "narration": "Its fangs punch straight through thick bone and turtle shells as easily as teeth piercing an empty soda can.", "visual_keyword": "big cat predator eyes intense", "stock_query": "jaguar eyes", "visual_prompt": "Golden eyes of a jaguar locked onto its target", "estimated_seconds": 4.8},
                        {"scene_id": 6, "narration": "Swimming silently without a single ripple, the big cat lunges onto the reptile's armored back.", "visual_keyword": "jaguar swimming water stealth", "stock_query": "jaguar swimming", "visual_prompt": "Jaguar gliding silently through deep river water", "estimated_seconds": 4.6},
                        {"scene_id": 7, "narration": "Its thick canines punch directly between the caiman's ears, piercing the brain and ending the fight in one second.", "visual_keyword": "jaguar splashing river ambush", "stock_query": "jaguar hunting", "visual_prompt": "Explosive water splash as the jaguar secures its prey", "estimated_seconds": 4.9},
                        {"scene_id": 8, "narration": "Then the apex cat drags the two-hundred-pound reptile straight up a vertical riverbank into the jungle.", "visual_keyword": "jaguar resting jungle tree", "stock_query": "jaguar jungle", "visual_prompt": "Dominant jaguar in the dense South American rainforest", "estimated_seconds": 4.6},
                    ],
                }

        vf = "long" if str(video_format or "short").strip().lower() in ("long", "long_form", "long-form", "16:9", "landscape", "horizontal", "documentary") else "short"
        if vf == "long":
            # Mở rộng kịch bản dự phòng thành 16 phân cảnh (5 Chương Phim Tài Liệu 16:9, ~360+ từ)
            base_scenes = list(raw_data.get("scenes") or [])
            expanded_scenes = []
            for idx, sc in enumerate(base_scenes):
                sc_copy = dict(sc)
                sc_copy["scene_id"] = len(expanded_scenes) + 1
                sc_copy["estimated_seconds"] = 8.5
                if idx == 0:
                    sc_copy["overlay_badge"] = "[ // CHAPTER 1: THE ANOMALY // ]"
                elif idx == 2:
                    sc_copy["overlay_badge"] = "[ // CHAPTER 2: HARD EVIDENCE // ]"
                elif idx == 4:
                    sc_copy["overlay_badge"] = "[ // CHAPTER 3: HIDDEN MECHANISM // ]"
                elif idx == 6:
                    sc_copy["overlay_badge"] = "[ // CHAPTER 4: THE CLIMAX // ]"
                expanded_scenes.append(sc_copy)

                # Thêm cảnh phân tích chuyên sâu đi kèm mỗi cảnh gốc để đạt 16 cảnh (~380 từ)
                deep_narr = (
                    f"Os pesquisadores documentaram cada detalhe físico desse fenômeno extremo, revelando dados que desafiam tudo o que a ciência conhecia até hoje."
                    if is_pt else
                    f"Field researchers documented every physical detail of this extreme phenomenon, uncovering biomechanical data that challenges conventional science."
                )
                if idx == 1:
                    deep_narr = (
                        "Análises de alta velocidade mostram que a força gerada nesse impacto supera qualquer outro predador do mesmo ecossistema natural."
                        if is_pt else
                        "High-speed telemetry reveals that the physical force generated during this encounter surpasses almost every rival in the ecosystem."
                    )
                elif idx == 3:
                    deep_narr = (
                        "Ao longo de milhões de anos de evolução implacável, essa adaptação biológica transformou a espécie em uma máquina de sobrevivência perfeita."
                        if is_pt else
                        "Across millions of years of relentless evolution, this biological adaptation turned the subject into an unstoppable survival machine."
                    )
                elif idx == 5:
                    deep_narr = (
                        "Mesmo sob condições extremas de pressão e isolamento, o sistema nervoso responde em frações de segundo sem perder a precisão."
                        if is_pt else
                        "Even under extreme environmental pressure, the neurological reflex fires within milliseconds without losing a fraction of precision."
                    )
                elif idx == 7:
                    deep_narr = (
                        "Essa descoberta extraordinária prova que a natureza guarda segredos letais muito além do que os olhos humanos conseguem enxergar."
                        if is_pt else
                        "This extraordinary discovery proves that the natural world still hides lethal mechanisms far beyond what the naked eye can see."
                    )
                expanded_scenes.append({
                    "scene_id": len(expanded_scenes) + 1,
                    "narration": deep_narr,
                    "visual_keyword": f"{sc.get('visual_keyword', 'documentary nature')} closeup detail",
                    "stock_query": sc.get("stock_query", "nature documentary"),
                    "visual_prompt": f"Cinematic 16:9 widescreen documentary shot: {sc.get('visual_prompt', '')}",
                    "estimated_seconds": 8.5,
                    "overlay_badge": "[ // CHAPTER 5: FINAL VERDICT // ]" if idx == 7 else None,
                })
            raw_data["scenes"] = expanded_scenes
            raw_data["title"] = re.sub(r"\s*#shorts\b", "", str(raw_data.get("title") or ""), flags=re.IGNORECASE).strip()
            raw_data["tags"] = [t for t in (raw_data.get("tags") or []) if str(t).lower() != "shorts"] + ["documentary"]
            raw_data["video_format"] = "long"

        raw_data["full_narration"] = " ".join(s["narration"] for s in raw_data["scenes"])
        return self._parse_json_script(json.dumps(raw_data, ensure_ascii=False), channel_niche=channel_niche, topic=topic, video_format=vf)

    def refine_script(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        video_format: Optional[str] = None,
    ) -> ScriptContract:
        """
        Tiếp thu góp ý của người dùng/Critic để tinh chỉnh phẫu thuật (Surgical Refinement) kịch bản hiện tại chuẩn viral.
        """
        vf = str(video_format or getattr(current_script, "video_format", "short") or "short").strip().lower()
        is_long_form = vf in ("long", "long_form", "long-form", "16:9", "landscape", "horizontal", "documentary")
        vf = "long" if is_long_form else "short"

        lang_instruction = build_language_integrity_instruction(language)
        lang_clean = str(language or "en").strip().lower()
        if lang_clean.startswith("vi"):
            target_lang_display = "TIẾNG VIỆT"
        elif lang_clean.startswith("pt"):
            target_lang_display = "PORTUGUÊS BRASILEIRO"
        else:
            target_lang_display = "ENGLISH"

        director_block = ""
        if director_notes and len(director_notes.strip()) > 3:
            director_block = f"\nCREATOR DIRECTOR NOTES (MUST PRESERVE):\n\"{director_notes.strip()}\"\n"

        from app.services.niche_manager import build_million_view_showrunner_prose_dna, get_niche
        from app.services.content_linter import ScriptValidationLinter
        prose_dna_block = build_million_view_showrunner_prose_dna(channel_niche, language=language)
        niche_profile = get_niche(channel_niche)

        # Nếu là kịch bản Phim Tài Liệu 10 Chương (Long-Form >= 28 cảnh), thực hiện Tinh Chỉnh Phẫu Thuật Theo Cảnh (Surgical Scene Patch)
        # để giữ nguyên cấu trúc 40 cảnh / ~1,500 từ, tuyệt đối không làm co rút kịch bản xuống 16 cảnh!
        if is_long_form and len(getattr(current_script, "scenes", []) or []) >= 28:
            patch_prompt = f"""You are an Executive Showrunner for a 10-Minute Widescreen 16:9 YouTube Documentary in {target_lang_display}.
We have a 40-scene (10-Chapter) documentary script for Topic: "{getattr(current_script, 'topic', '') or current_script.title}" (Niche: '{channel_niche}').
Our Quality Critic flagged specific surgical improvements needed:
"{feedback}"
{director_block}
{lang_instruction}
{prose_dna_block}

CURRENT SCENE 1 HOOK & KEY SCENES SUMMARY:
- Title: "{current_script.title}"
- Thumbnail Text: "{current_script.thumbnail_text}"
- Scene 1 (Cold-Open Hook): "{current_script.scenes[0].narration if current_script.scenes else current_script.hook}"
- Sample Scenes (1 to 20):
{chr(10).join(f'  Scene {s.scene_id} [vk={s.visual_keyword}]: "{s.narration}"' for s in current_script.scenes[:20])}

SURGICAL PATCH INSTRUCTIONS:
1. Keep the 40-scene documentary structure intact! Provide an upgraded `hook` (and `scene_id: 1` narration, 18-28 words, declarative, vivid in-medias-res Cold Open, ZERO '?' questions) and any 2 to 6 specific `scene_patches` (`scene_id`, `narration`, `visual_keyword`, `stock_query`) needed to fix repetition, pacing, or visual queries flagged in the critique above.
2. For non-psychology niches (`history_mystery`, `wildlife_predators`, `deep_sea_earth_mysteries`, `alternate_history`), NEVER use "2D doodle", "symbolic hourglass", or "brain scan animation" in `visual_keyword` or `stock_query`—use 100% authentic archival, historical, or wildlife 16:9 documentary queries!
3. `thumbnail_text` MUST be 2 to 3 ALL-CAPS words with ZERO emojis.

Return ONLY valid JSON:
{{
  "title": "{current_script.title}",
  "thumbnail_text": "2-3 ALL-CAPS WORDS NO EMOJI",
  "hook": "Upgraded 18-28 word declarative Cold-Open Hook for Scene 1 in {target_lang_display}",
  "scene_patches": [
    {{
      "scene_id": 1,
      "narration": "Upgraded 18-28 word declarative Cold-Open Hook for Scene 1 in {target_lang_display}",
      "visual_keyword": "concrete 3-5 word English 16:9 documentary query",
      "stock_query": "2-4 word English stock query"
    }}
  ]
}}"""
            try:
                raw_patch = self._call_api(patch_prompt, json_mode=True, timeout=30, max_total_seconds=45)
                s_idx = raw_patch.find("{")
                e_idx = raw_patch.rfind("}")
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    p_data = json.loads(raw_patch[s_idx : e_idx + 1])
                    updated_dump = current_script.model_dump()
                    if p_data.get("title"):
                        updated_dump["title"] = str(p_data["title"]).strip()
                    if p_data.get("thumbnail_text"):
                        updated_dump["thumbnail_text"] = re.sub(r"[^\w\s\?\!\.\,\-\'\"À-ỹ]", "", str(p_data["thumbnail_text"])).strip().upper()
                    patches_by_id = {
                        int(sp.get("scene_id")): sp
                        for sp in (p_data.get("scene_patches") or [])
                        if isinstance(sp, dict) and sp.get("scene_id")
                    }
                    for sc_dict in updated_dump.get("scenes", []):
                        sid = int(sc_dict.get("scene_id") or 0)
                        if sid in patches_by_id:
                            sp = patches_by_id[sid]
                            if sp.get("narration") and len(str(sp["narration"]).split()) >= 12:
                                sc_dict["narration"] = str(sp["narration"]).strip().replace("?", ".") if sid == 1 else str(sp["narration"]).strip()
                            if sp.get("visual_keyword"):
                                sc_dict["visual_keyword"] = str(sp["visual_keyword"]).strip()
                            if sp.get("stock_query"):
                                sc_dict["stock_query"] = str(sp["stock_query"]).strip()
                    if p_data.get("hook") and len(str(p_data["hook"]).split()) >= 10 and 1 not in patches_by_id:
                        clean_h = str(p_data["hook"]).strip().replace("?", ".")
                        updated_dump["hook"] = clean_h
                        if updated_dump.get("scenes"):
                            updated_dump["scenes"][0]["narration"] = clean_h
                    elif updated_dump.get("scenes"):
                        updated_dump["hook"] = updated_dump["scenes"][0]["narration"]
                    updated_dump["full_narration"] = " ".join(s.get("narration", "") for s in updated_dump.get("scenes", []))
                    return self._parse_json_script(
                        json.dumps(updated_dump, ensure_ascii=False),
                        channel_niche=channel_niche,
                        topic=getattr(current_script, "topic", "") or current_script.title,
                        video_format="long",
                    )
            except Exception as e_lp:
                logger.warning(f"[ContentAgent] Long-form surgical patch fallback: {e_lp}")
                return current_script

        prompt = f"""
You are an elite viral YouTube {'Widescreen 16:9 Documentary' if is_long_form else 'Shorts'} Master Showrunner and Human Creator Voice Specialist.
We have an existing script (format: {vf.upper()}), and our Quality Critic / Creator provided specific surgical revisions:

CRITIQUE & REVISION FEEDBACK:
"{feedback}"
{director_block}
{prose_dna_block}
CURRENT SCRIPT:
{current_script.model_dump_json(indent=2)}

SURGICALLY REFINE the script strictly addressing the feedback above while matching the YAML `GOLD-STANDARD PROSE CADENCE EXAMPLE` above!
Target Language: {language} ({target_lang_display})
{lang_instruction}

HARD RETENTION & AUTHENTIC VIRAL CREATOR SPOKEN VOICE RULES:
0. GOLDEN SEPARATION OF DUTY, KITCHEN-TABLE SPOKEN VOICE & EVERYDAY CLARITY (ZERO DRY PHYSICS UNITS & ZERO AI ESSAY PROSE):
   - TITLE RULE (NEVER VIOLATE): Keep exact, high-search-volume entity names in `title` (28-55 chars, ZERO `#hashtags`, e.g., `Sea Turtle vs Box Jellyfish: Why Deadly Tentacles Don't Faze It 🐢🪼`). NEVER drop a primary entity from `title`, AND NEVER put dry textbook physics units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`) in `title`!
   - SCENE 1 HOOK (0.0s) HARD RULE (COMPLEMENT THE TITLE WITH INSTANT ACTION/PARADOX — ZERO TITLE QUESTION ECHO & NAME BOTH ENTITIES IN 'A VS B'):
     * Explicitly name the primary entity/combatants in complete spoken grammar ({'8-14 words' if is_long_form else '6-12 words'}) AND open with an immediate physical clash, lethal mechanism strike, or upside-down survival paradox!
     * 🚨 ZERO TITLE-TO-HOOK QUESTION ECHO: If `title` starts with `"Why / Por Que / Como / How"`, Scene 1 (`hook` and `scenes[0].narration`) MUST NOT start with `"Why / Por que / Como / How"` or repeat the title question! Drop straight into the visceral action at 0.0s (e.g., if Title is `"Por Que Esse Lago No Fundo Do Mar Paralisa Tubarões?"`, Scene 1 is `"Nadar dentro dessa mancha escura no fundo do mar congela um tubarão na hora!"`).
     * MANDATORY 2-ENTITY RULE: Whenever `title` or the topic pits two entities against each other (`Entity A vs Entity B`), Scene 1 (`hook` and `scenes[0].narration`) MUST explicitly name BOTH `Entity A` AND `Entity B` right in Scene 1 (e.g., `"Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."`, `"Encarar uma mamba-negra de frente é morte certa—menos pro mangusto."`)! NEVER hide the second entity until Scene 2!
     * ZERO WEAK YES/NO QUESTIONS IN SCENE 1: NEVER start Scene 1 with weak closed Yes/No questions like `"Have you ever seen...?"`, `"Did you know...?"`, `"Você já viu...?"`, or `"Você sabia...?"`.
   - STRICT SCENE COUNT & WORD COUNT LOCK:
     * For SHORTS (`video_format="short"`): You MUST output **STRICTLY 7 OR 8 COMPLETE SCENES** (total 95 to 132 words across all 7-8 scenes: Scene 1 = 6-14 words; Scenes 2-7/8 = 12-18 words each). NEVER output 6 scenes or 9 scenes!
   - THUMBNAIL TEXT HARD RULE: `thumbnail_text` MUST be strictly 2 to 3 ALL-CAPS words in {target_lang_display} with **ZERO EMOJIS** (e.g. `"BOTE FATAL!"`, `"ZERO AIR"`, `"STAY SILENT."`).
   - 🗣️ KITCHEN-TABLE SPOKEN CREATOR VOICE (ZACK D. FILMS / CASUAL GEOGRAPHIC / CHARISMA ON COMMAND / IFINITY WHAT / THE DODO / VOCÊ SABIA?):
     * CRITICAL: Read `CRITIQUE & REVISION FEEDBACK` above carefully! Every single phrase quoted after `VIRAL CREATOR VOICE CHECK FAILED` MUST be 100% DELETED and rewritten into effortless spoken human YouTuber language!
     * In English, ALWAYS use **at least 3–5 natural spoken contractions** (`it's`, `doesn't`, `don't`, `can't`, `you'd`, `didn't`, `would've`, `hadn't`, `wasn't`, `that's`, `they're`, `he'd`) and plain `$1` everyday words!
     * In `pt-BR`, speak raw, natural Brazilian YouTuber `"conversa de bar"` Portuguese (`pra`, `pro`, `tá`, `bicho`, `na hora`, `só que`, `em vez de`, `olha só`, `dentro d'água` — NEVER overuse clichés like `num piscar de olhos`, NEVER use formal written enclitics like `imobilizá-lo` or `matá-lo`, and NEVER use stiff TV-documentary dub clichés like `golpe cirúrgico`, `alicate hidráulico`, `Enquanto outros felinos...`, `Felinos comuns`, `desligando o sistema nervoso`, or `deixando só o silêncio... pra trás`!).
     * STRICTLY FORBIDDEN AI TELLS: Front-loaded participial openers (`"Equipped with..."`, `"Armed with..."`, `"Boasting..."`, `"Ostentando..."`, `"Reaching down into..."`), generic encyclopedia setups (`"Most predators go for..."`, `"Enquanto outros..."`, `"Most people assume..."`, `"Muita gente acha que..."`, `"No fim das contas"`, `"Studies show..."`), hardware/medical/sports-gear clichés on animals (`"built-in shin guards"`, `"built-in armor"`, `"like a riot shield"`, `"body armor"`, `"pile-driver"`, `"surgical strike"`, `"golpe cirúrgico"`, `"movimento cirúrgico"`, `"almoço rápido"`, `"hydraulic pliers / press"`, `"alicate hidráulico"`, `"It's a high-stakes game of..."`, `"It's a brutal, silent..."`), clinical/biochemistry hand-waving (`"tiny cells"`, `"special flexible fats"`, `"air bladders"`, `"molecules"`, `"proteins"`, `"chemical buffers"`, `"elapid"`, `"permeates"` — say `"trapped air pockets"` instead of `"air bladders"`!), Wikipedia calendar date-stamp Scene-1 openers (`"In October 1962, ..."`, `"In 1961, ..."`), unnamed pronoun Scene-1/Title openers (`"He wouldn't leave..."`, `"He Guarded..."` — ALWAYS name `"this stray dog"` / `"this puppy"` explicitly!), bureaucratic rulebook transitions & `$10` history words (`"Under ... rules"`, `"required unanimous agreement"`, `"required agreement"`, `"kiloton"`, `"megaton"`, `"leaving ... as the final roadblock"`, `"held absolute veto power"`, `"dissent"`, `"unanimous"`, `"vessel"`, `"full-scale atomic retaliation"`, `"nuclear apocalypse"`, `"nuclear holocaust"`, `"vigil"`, `"didn't budge"`, `"exact same"` — tell rules through active human keys/votes with 5+ contractions starting in Scenes 1–3!), forced `"acting like..."` similes, and essay/proverb endings (`"It's the ultimate survival trick"`, `"Real social power belongs to..."`, `"That is how a..."`, `"That's why X belongs to..."`, `"domina o terreno porque..."`, `"They never fight the crushing abyss"`, `"the hunter becomes the meal"`, `"O Pantanal pune quem falha"`).
     * SAVE THE SHARPEST IRONIC TWIST OR CONCRETE PHYSICAL AFTERMATH FOR THE VERY LAST SCENE! Never bury the surface-pressure/ironic twist in Scene 5 and end Scene 8 on generic description!
1. SCENE 1 PATTERN INTERRUPT HOOK (CRITICAL TO PREVENT SWIPING):
   - Scene 1 spoken text must establish immediate high curiosity with a declarative physical clash, lethal mechanism strike, or upside-down 2-entity survival paradox ({'8-14 words' if is_long_form else '6-12 words, ZERO weak Yes/No questions and ZERO Title Question Echo'}).
2. SMOOTH, COMPLETE CONVERSATIONAL NARRATIVE ({'360-560 words total across 16-22 scenes (5 Chapters), 18-26 words per body scene' if is_long_form else '92-116 words total across STRICTLY 7 OR 8 SCENES (NEVER 6 SCENES), dynamic 12-16 words per body scene'} — NEVER write choppy or truncated sentences!):
   - Act 1 (Scene 1): Declarative spoken 0.0s action/paradox Hook naming explicit entities ({'8-14 words' if is_long_form else '6-12 words'}) without repeating the title question.
   - Act 2 (Scenes 2-3): Direct action, sharp contrast, or exact real-life spoken quote + plain `$1` mechanism.
   - Act 3 (Scenes 4-5): 15-Second Plot Twist (or `"What If"` counterfactual domino chain if topic asks `"What If"`) + natural assumption-flip.
   - Act 4 (Scenes 6-7): Concrete physical payoff or exact spoken Comeback in quotation marks (`"..."`).
   - Act 5 (Scene 7 or 8): Fresh, concrete, or ironic main-clause closing realization without copying Scene 1 (ZERO spoken Subscribe CTA).
3. CONTEXT-ANCHORED, SEARCH-ENGINE-NATIVE `visual_keyword` & `stock_query` (ZERO PRONOUNS, ZERO GENERIC TEXTURES, ZERO METAPHOR PROPS):
   - Resolve all pronouns ("It", "He", "They") in every scene's `visual_keyword` (3-5 English words formatted like real YouTube Shorts titles, e.g. `"leopard attacking porcupine"`, `"porcupine quills closeup"`, NEVER unsearchable narrative phrases like `"porcupine walking away"`) and `stock_query` (2-3 English words using common canonical noun + habitat for Pexels/Pixabay, e.g. `"porcupine wildlife"`, `"leopard hunting"`, NEVER generic textures like `"sharp needle texture"` or `"microscopic texture"`).
4. Return ONLY valid JSON with STRICTLY {'16 to 22' if is_long_form else '7 or 8'} scene objects in `"scenes"`:
{{
  "video_format": "{vf}",
  "title": "Viral YouTube {'Documentary' if is_long_form else 'Shorts'} Title with Emojis",
  "primary_entity": "Full official name of main person/figure/species",
  "hook": "The opening hook sentence ({'8-14 words' if is_long_form else '6-12 words — declarative physical action, lethal mechanism strike, or sharp paradox naming explicit entities, NEVER repeating a Why/Por que title question'})",
  "hook_visual_mode": "video",
  "is_abstract_keyword": false,
  "climax_scene_id": {8 if is_long_form else 4},
  "hook_search_queries": ["concrete short search query 1", "concrete short search query 2", "concrete short search query 3"],
  "full_narration": "The full, smooth, well-developed spoken story without stage directions ({'360-560 words across 16-22 scenes' if is_long_form else '92-116 words across 7-8 scenes'} with Real-Life Example, Mechanism, Counter-Move, and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-3 BOLD ALL-CAPS WORDS IN {target_lang_display} WITH ZERO EMOJIS",
  "scenes": [
    {{
      "scene_id": 1,
      "subject_entity": "Exact canonical English name of the entity/subject acting in THIS scene",
      "visual_reasoning": "Narrative and visual intent of this scene",
      "narration": "Complete, natural sentence spoken in this scene ({'18-26 words per body scene, 16-22 scenes total' if is_long_form else 'Scene 1: 6-12 words declarative physical action or sharp 2-entity paradox (NEVER repeat a Why/Por que title question, NO weak Yes/No questions); Body scenes 2 through 7/8: 12-16 words each, STRICTLY 7 OR 8 SCENES TOTAL'})",
      "visual_keyword": "simple relevant search query for the subject/theme (2-5 English words)",
      "visual_prompt": "Cinematic visual description fitting the overall story mood",
      "stock_query": "simple subject/mood query for stock video (2-4 English words)",
      "search_queries": [
        "1st query: [Scene Subject] + [Exact Scene Action] (3-4 English words)",
        "2nd query: [Scene Subject] + [Natural Habitat / B-Roll Pose] (2-4 English words)",
        "3rd query: [Stock-Friendly Synonym / Broader Species Name] + [Habitat/Action] (2-3 English words)"
      ],
      "media_type": "video",
      "camera_motion": "zoom_in_fast | ken_burns_zoom_out | pan_right | ken_burns_zoom_in | pan_left",
      "sfx_cue": "impact_boom | whoosh_heavy | riser_tension | heartbeat_thump | glitch | reveal",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "[ // THE REAL TRAP // ] or null",
      "estimated_seconds": {8.5 if is_long_form else 4.0}
    }}
  ],
  "tags": ["tag1", "tag2", "{'documentary' if is_long_form else 'shorts'}"],
  "description": "Engaging description with hashtags",
  "pinned_comment": "12-22 word Counterfactual 50/50 Debate Question ending with (1) ... or (2) ... 👇 in {target_lang_display}",
  "viral_formula": "Seamless Infinite Loop | Micro-Hook Escalation | Pattern Interrupt"
}}
"""
        raw_text = self._call_api(prompt, json_mode=True, timeout=45 if is_long_form else 30)
        refined = self._parse_json_script(
            raw_text,
            channel_niche=channel_niche,
            topic=getattr(current_script, "topic", "") or getattr(current_script, "title", ""),
            video_format=vf,
        )
        lint_res = ScriptValidationLinter.lint(
            script=refined,
            niche_profile=niche_profile,
            target_wpm=niche_profile.get("target_wpm", 170),
        )
        if not lint_res.is_valid:
            logger.warning(
                f"⚠️ [ContentAgent.refine_script] Bản tinh chỉnh vi phạm Linter ({'; '.join(lint_res.issues)}) -> Chạy 1 lượt sửa lỗi Linter tức thì..."
            )
            fix_prompt = (
                f"{prompt}\n\n🚨 CRITICAL LINTER VIOLATIONS TO FIX IMMEDIATELY:\n"
                f"{'; '.join(lint_res.issues)}\n"
                f"{lint_res.retry_prompt_hint or ''}\n"
                f"Output ONLY the corrected JSON with STRICTLY {'16-22' if is_long_form else '7 or 8'} scenes and ZERO '?' in Scene 1!"
            )
            try:
                raw_fix = self._call_api(fix_prompt, json_mode=True, timeout=30)
                refined_fix = self._parse_json_script(
                    raw_fix,
                    channel_niche=channel_niche,
                    topic=getattr(current_script, "topic", "") or getattr(current_script, "title", ""),
                    video_format=vf,
                )
                if ScriptValidationLinter.lint(script=refined_fix, niche_profile=niche_profile).is_valid:
                    return refined_fix
            except Exception as e_rf:
                logger.debug(f"[ContentAgent.refine_script] Linter retry skip: {e_rf}")
        return refined

    def chat_brainstorm(
        self,
        session_id: str,
        user_message: str,
        channel_niche: str = "general",
        language: str = "en"
    ) -> str:
        """
        Thảo luận, phản biện và đồng sáng tạo ý tưởng Shorts với người dùng.
        Lưu lịch sử hội thoại cho session_id và tự động xoay key nếu quá tải.
        """
        if session_id not in self.conversations:
            self.conversations[session_id] = []

        history = self.conversations[session_id]

        system_instruction = f"""
Bạn là Giám đốc Sáng tạo & Đạo diễn YouTube Shorts triệu view hàng đầu thế giới (kênh ngách: '{channel_niche}').
Nhiệm vụ của bạn là đồng hành, tư duy cùng người dùng (và nhóm sáng tạo) để thảo luận, phản biện, tìm góc nhìn viral độc lạ và hoàn thiện kịch bản Shorts.

Phong cách làm việc:
1. Trả lời bằng ngôn ngữ: {language} (ngắn gọn, súc tích, truyền cảm hứng, đi thẳng vào bản chất).
2. Luôn chú trọng:
   - HOOK 3 giây đầu (yếu tố sống còn giữ chân người xem).
   - Nhịp điệu kịch tính (Pacing), góc nhìn gây tranh cãi hoặc bất ngờ (Pattern Interrupt).
   - Kêu gọi tương tác (Call to Action / Open Loop) để người xem bình luận sôi nổi.
3. Khi người dùng đưa ra một chủ đề hoặc câu hỏi, hãy:
   - Phân tích ngắn gọn vì sao chủ đề này ăn view hoặc điểm yếu cần khắc phục.
   - Đưa ra 2-3 gợi ý Hook giật gân khác nhau.
   - Đề xuất cốt truyện 30-45 giây súc tích.
4. Trả lời dưới dạng định dạng text rõ ràng, có emoji sinh động, không quá dài dòng (dưới 350 từ để đọc dễ trên Telegram).
"""
        recent_history = history[-10:]
        formatted_dialogue = []
        for turn in recent_history:
            role_label = "Creator" if turn["role"] == "user" else "Director"
            formatted_dialogue.append(f"{role_label}: {turn['content']}")
        formatted_dialogue.append(f"Creator: {user_message}")

        full_prompt = f"{system_instruction}\n\nLỊCH SỬ THẢO LUẬN:\n" + "\n".join(formatted_dialogue) + "\n\nDirector:"

        try:
            reply_text = self._call_api(full_prompt, json_mode=False, timeout=20)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "model", "content": reply_text})
            if len(history) > 20:
                self.conversations[session_id] = history[-20:]
            return reply_text
        except Exception as e:
            logger.warning(f"Lỗi khi chat Gemini: {e}")
            err_str = str(e)
            if "quota" in err_str.lower() or "429" in err_str:
                return "⚠️ Các Gemini API Key trong pool tạm thời đạt giới hạn request/phút. Vui lòng thử lại sau 15-30 giây!"
            return f"⚠️ Lỗi kết nối AI: {err_str[:120]}. Vui lòng thử lại!"

    def convert_discussion_to_script(
        self,
        session_id: str,
        channel_niche: str = "general",
        language: str = "en"
    ) -> ScriptContract:
        """
        Chuyển đổi toàn bộ tinh hoa từ đoạn chat thảo luận thành một kịch bản phân cảnh chuẩn ScriptContract.
        """
        history = self.conversations.get(session_id, [])
        if not history:
            return self.generate_script(topic="Viral Shorts Concept", channel_niche=channel_niche, language=language)

        dialogue_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-8:]])

        lang_instruction = build_language_integrity_instruction(language)
        target_lang_display = "TIẾNG VIỆT" if str(language).lower().startswith("vi") else "ENGLISH"

        prompt = f"""
You are an expert YouTube Shorts producer and cinematographer.
Based on the following brainstorm discussion between a creator and director:
---
{dialogue_text}
---

Synthesize this entire discussion into a complete, high-retention 40-50 second YouTube Shorts script.
Niche: '{channel_niche}'
Target Language: {language} ({target_lang_display})
{lang_instruction}

HARD RULES:
1. SCENE 1 HOOK (PREVENT SWIPING): Choose an electric 5-8 word pattern-interrupt hook (Warning, Elite Move, Dark Law, or Counter-Intuitive Shock). Never passive or textbook.
2. Fast-paced, punchy, concise sentences.
3. visual_prompt must depict the ACTUAL PHENOMENON, never the literal meaning of a metaphor:
   - narration: "like a mountain crushed to sand" → visual_prompt: stellar collapse, NOT a mountain
   - Each scene: visually UNIQUE subject, angle, and lighting from all others.
4. visual_prompt format: "[Subject + action], [camera angle], [lighting], [environment detail]"
5. stock_query: 2-3 English topic words for Pexels ONLY. FORBIDDEN: movie titles, actor names, director names.
   BAD: "Interstellar Nolan voyage" | GOOD: "black hole space" or "galaxy explosion"
6. Return ONLY valid JSON:
{{
  "title": "Viral YouTube Shorts Title with Emojis",
  "hook": "The opening 3-second hook sentence",
  "full_narration": "The full spoken text without stage directions",
  "scenes": [
    {{
      "scene_id": 1,
      "narration": "Spoken text in this scene",
      "visual_keyword": "black hole event horizon",
      "visual_prompt": "Detailed cinematic scene: subject + camera angle + lighting + environment",
      "stock_query": "black hole",
      "estimated_seconds": 4
    }}
  ],
  "tags": ["tag1", "tag2", "shorts"],
  "description": "Engaging description with hashtags"
}}
"""
        raw_text = self._call_api(prompt, json_mode=True, timeout=30)
        return self._parse_json_script(raw_text, channel_niche=channel_niche)
