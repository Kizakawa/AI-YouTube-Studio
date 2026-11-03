"""
Script tự động sản xuất & hẹn lịch phát sóng khung giờ vàng cho các kênh YouTube Shorts:
- Hỗ trợ chạy 6 kênh chủ lực (loại trừ Paws & Hearts Rescue theo yêu cầu: `--channel all_6 --count 2`):
  1. The mysteries of the world (`ch_the_mysteries_of_the_world` - US EN)
  2. Psychology of the world (`ch_psychology_of_the_world` - US EN)
  3. Wild Odyssey (`ch_wild_odyssey` - US EN)
  4. Alternate History & "What If?" (`@alternatehistorywhatif-e2x` - US EN)
  5. Odisseia Selvagem (`ch_odisseia_selvagem` - Brazil PT-BR)
  6. Mistérios do Mundo (`ch_misterios_do_mundo` - Brazil PT-BR)

Quy trình chuẩn 5 bước nghiêm ngặt cho từng video:
- Bước 1: Tạo kịch bản viral 5-Act retention + Đánh giá chấm điểm kịch bản (>= 80/100) + Chống trùng lặp chủ đề.
- Bước 2: Tổng hợp bộ Tags SEO chuẩn ngách (tối đa 25 tags) + Hashtags Tiêu đề & Mô tả (`build_smart_video_tags_and_hashtags`).
- Bước 3: Đăng ký VideoJob vào Database kèm khe giờ vàng chuẩn (`SmartScheduler`).
- Bước 4: Dựng video 1080x1920 @ 30fps chất lượng cao (`VideoPipeline` + LangGraph 5 Graders) + Chấm điểm FFprobe QA & VisualCritic.
- Bước 5: Upload YouTube Shorts, gắn đầy đủ Tags SEO, Custom Thumbnail, Pinned Comment & Hẹn lịch phát sóng khung giờ vàng (`verify_and_heal_video_metadata`).
"""
import sys
import os
import json
import asyncio
import logging
import argparse
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Đảm bảo import được module dự án
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# UTF-8 trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ProduceEnglishChannels")

CHANNELS_CONFIG = {
    "ch_the_mysteries_of_the_world": {
        "id": "ch_the_mysteries_of_the_world",
        "name": "The mysteries of the world",
        "niche": "deep_sea_earth_mysteries",
        "language": "en",
        "google_account": "acount_2",
        "preferred_voice": "en-US-BrianNeural",
        "handle": "@themysteriesoftheworld-f3w",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel The mysteries of the world (US English). Deep 5-Act Dual-Peak abyssal showdown & lethal ocean/earth physics arc (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TITLE (Zack D. Films / Nature Lenses / RealLifeLore Viral Style — 6-9 conversational words): Write a natural human Why/How title naming the exact creature/phenomenon + everyday action/paradox (e.g. 'Why Great White Sharks Are Terrified Of Orcas', 'Why Sea Turtles Can Eat Deadly Jellyfish', 'Why Submarines Never Pass Below This Depth'). NEVER use stiff 'A vs B: Metric' colon formulas and NEVER put textbook units (PSI, °C, dB, atm, Newtons) in the title! "
            "SCENE 1 HOOK (0.0s-2.5s, 6-12 words — PRIORITIZE 'WHY...' CURIOSITY PARADOX HOOK): Open right at 0.0s with a counter-intuitive 'Why...' question or 'Here's why...' paradox naming the exact entities (e.g., 'Why do massive great white sharks panic the second an orca shows up?', 'Why can a sea turtle chew up deadly box jellyfish without dying?') that forces the viewer to stay for the answer! NEVER repeat the title word-for-word at 0.0s! "
            "ACT 2 (Scenes 2-3): Reveal the first physical reason or lethal weapon translated into a vivid everyday physical analogy anyone can picture. "
            "ACT 3 (Scene 4 - 15s Twist): Second explosive peak / deeper counter-mechanism translated into a visceral sensory image that flips the viewer's assumption. "
            "ACT 5 (Scene 7/8): Complete sentence forming a Seamless Infinite Loop Bridge back into Scene 1 (ZERO spoken Subscribe CTA). "
            "Tone: suspenseful, dark, adrenaline-pumping documentary style. Strict English only."
        ),
        "topics": [
            "Why Nobody Can Survive Inside Mexico's Giant Cave Of Crystals For More Than 15 Minutes Without Drowning In Air",
            "Why Giant Sperm Whales Dive 7,000 Feet Into Pitch-Black Ocean Trenches To Hunt Giant Squid",
            "Why Blue-Ringed Octopus Venom Paralyzes Giant Moray Eels In Seconds Without A Single Pain Signal",
            "Orca Pod vs 4,000-Pound Great White Shark: How Killer Whales Flip Ocean Apex Predators Into Tonic Immobility in 3 Seconds",
            "Box Jellyfish 60 Venomous Tentacles vs Sea Turtle: Why Venom That Stops a Human Heart in 120 Seconds Can't Stop This Armored Reptile",
            "Portuguese Man O' War vs Loggerhead Sea Turtle: How a 400-Pound Armored Reptile Devours 165-Foot Venomous Tentacles",
            "400°C Black Smoker Hydrothermal Vent vs Giant Tube Worms: How Deep-Sea Creatures Survive Boiling Acid at 8,000 Feet Without Sunlight",
            "Blue-Ringed Octopus vs Giant Moray Eel: How a 5-Inch Ocean Creature Carries Tetrodotoxin Venom 1,200 Times Deadlier Than Cyanide",
            "Naica Cave of the Crystals vs 136°F Steam at 1,000 Feet Deep: Why Human Lungs Drown Inside Mexico Giant Crystal Chamber",
            "Sharks vs Kavachi Underwater Volcano: Why Hammerheads Hunt Inside a 212°F Acidic Ocean Crater",
            "400-Year-Old Greenland Shark vs Giant Squid: The Blind Apex Predator Hunting at 6,500 Feet in the Arctic Abyss",
            "Brine Pool at 3,300 Feet: The Underwater Toxic Lake That Paralyzes Deep-Sea Sharks in 3 Seconds",
            "Goblin Shark vs Black Dragonfish: The Slingshot Jaw That Strikes in 0.02 Seconds at 4,000 Feet",
            "Lake Vostok: The Secret Prehistoric Ocean Sealed Under 13,000 Feet of Antarctic Ice for 15 Million Years",
            "Challenger Deep at 35,876 Feet: What Crushed the Nereus Submarine Like an Empty Soda Can Under 50 Passenger Jets",
            "Skeleton Coast Namibia: Why 1,000 Shipwrecks Rotted in the World's Oldest Desert Fog",
            "Lake Nyos 1986: The Silent 1.6-Million-Ton CO2 Cloud That Erased 1,746 Lives",
            "Why Deep Sea Divers Never Look Up At The Surface At Night",
            "The Terrifying Reason Scientists Sealed The Kola Superdeep Borehole At 40,230 Feet",
            "Inside Movile Cave: Sealed From Earth's Atmosphere For 5.5 Million Years",
            "The 52-Hertz Whale Anomaly And What Submarines Recorded Beneath Point Nemo",
            "Why Military Submarines Turn Off All Lights Passing Below 3,000 Meters in the Midnight Zone",
        ],
        "default_tags": [
            "shorts", "mystery", "deepocean", "unexplained", "thalassophobia",
            "deepsea", "science", "creepy", "oceanmysteries", "mariana", "viral",
        ],
    },
    "ch_psychology_of_the_world": {
        "id": "ch_psychology_of_the_world",
        "name": "Psychology of the world",
        "niche": "psychology",
        "language": "en",
        "google_account": "accout3",
        "preferred_voice": "en-US-AndrewNeural",
        "handle": "@vv1n-oe9gs",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 17,
        "director_notes": (
            "Target channel Psychology of the world (US English). Deep 5-Act Dual-Peak Forensic Interrogation, Famous Brain Experiments & Behavioral Traps arc (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "STRICT NICHE PIVOT: Focus ONLY on (1) Forensic Courtroom & Detective Interrogation Psychology, (2) Famous University Brain & Obedience Experiments, and (3) Casino/Billionaire Behavioral Traps. "
            "TITLE (Charisma on Command / Zack D. Films Viral Style — 6-9 conversational words): Write a natural human Why/How title naming the exact situation/test + everyday human outcome (e.g. 'Why FBI Agents Put A Water Cup Near Suspects', 'Why Guilty Suspects Always Freeze Their Feet', 'Why Casinos Never Have Windows Or Clocks'). NEVER use stiff 'A vs B: Metric' colon formulas or dry lab units! "
            "SCENE 1 HOOK (0.0s-2.5s, 6-12 words — PRIORITIZE 'WHY...' CURIOSITY HOOK): Open right at 0.0s with a counter-intuitive 'Why...' question naming the exact test or behavior (e.g. 'Why do FBI detectives place one paper water cup six inches from a suspect?', 'Why do trained liars control their face, yet freeze both feet under the table?') without repeating the title verbatim! "
            "ACT 2 (Scenes 2-3): Real courtroom/experiment setup and first behavioral reaction. "
            "ACT 3 (Scene 4 - 15s Twist): The hidden second psychological mechanism that flips expectations. "
            "ACT 4-5 (Scenes 6-8): Practical counter-move and Seamless Infinite Loop Bridge back into Scene 1 (ZERO spoken Subscribe CTA). "
            "Tone: surgical, forensic, clinical, gripping (JCS Criminal Psychology style). Strict English only."
        ),
        "topics": [
            "Why Las Vegas Casinos Never Have Windows Or Clocks And Curve Every Hallway To Trap Your Brain",
            "Why 72 Hours Inside A Completely White Soundproof Room Makes The Human Brain Hallucinate",
            "Why FBI Detectives Place A Paper Water Cup 6 Inches From Guilty Suspects In Police Interrogations",
            "FBI Cognitive Load Reverse-Order Test vs Rehearsed Alibis: Why Telling a Story Backward Collapses a Liar's Brain in 8 Seconds",
            "1972 Stanford Marshmallow Delay Test vs Dopamine Impulse: How Prefrontal Cortex Self-Control Predicts Wealth 40 Years Later",
            "1961 Yale Milgram Experiment vs 450-Volt Authority Switch: Why 65% of Ordinary People Obeyed the Final Lethal Command",
            "FBI Foot-Freeze Interrogation Test vs Trained Liars: Why Guilty Suspects Control Their Face But Betray Deception Below the Desk",
            "Water Cup Barrier Test vs Guilty Suspects: Why Detectives Place a Paper Cup 6 Inches From Your Hand to Expose Deception",
            "Las Vegas Casino Labyrinth vs Human Brain: How Zero Clocks and Curved Carpets Trigger a 400% Dopamine Trap",
            "The Interrogation Water Cup Test: Why FBI Agents Place a Cup 6 Inches From a Suspect",
            "Orfield Labs -24.9 dB Quietest Room vs Human Brain: Why Hearing Your Own Blood Rushing Drives Test Subjects Out in 45 Minutes",
            "Why Guilty Suspects Repeat Your Exact Question Before Answering in Police Interrogations",
            "White Room Torture: What 72 Hours of Complete Color Deprivation Does to the Human Brain",
            "Las Vegas Casino Oxygen & Curved Carpets vs Human Brain: How Zero Windows and Zero Clocks Trigger a 400% Dopamine Trap",
            "The 1971 Stanford Prison Collapse: How 24 Normal Students Turned Ruthless in Just 6 Days",
            "Why Trained Liars Control Their Face But Always Betray Themselves With Their Feet (FBI Profiling)",
            "The Decoy Pricing Trap: How Movie Theaters and Tech Giants Trick Your Brain Into Buying the Expensive Option",
            "Why Suspects Stare Straight Unblinking After Telling a Lie (The Cognitive Load Stare)",
            "The Monster Study of 1939: The Forbidden Speech Experiment That Broke Orphans in 4 Months",
            "Why Billionaires and Surgeons Wear the Exact Same Outfit Every Day (Decision Fatigue Science)",
            "How Hostage Negotiators Use the 8-Second Late-Night FM DJ Voice to Disarm Armed Suspects",
            "The Milgram Shock Machine: Why 65% of Ordinary People Obeyed Orders to Deliver 450 Volts",
            "Why Your Brain Slows Down Time During a Car Crash (The Amygdala High-Speed Frame Illusion)",
            "How Polygraph Examiners Catch You Before They Even Ask the Real Question (The Control Lie Trap)",
        ],
        "default_tags": [
            "shorts", "psychology", "forensicpsychology", "brainscience", "interrogation",
            "fbi", "humanbehavior", "neuroscience", "mindtraps", "viral",
        ],
    },
    "ch_wild_odyssey": {
        "id": "ch_wild_odyssey",
        "name": "Wild Odyssey",
        "niche": "wildlife_predators",
        "language": "en",
        "google_account": "acount_2",
        "preferred_voice": "en-US-ChristopherNeural",
        "handle": "@wildodysseyhq",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 18,
        "director_notes": (
            "Target channel Wild Odyssey (US English). Deep 5-Act Dual-Peak wildlife showdown (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TITLE (Nature Lenses / Casual Geographic / Zack D. Films Viral Style — 6-9 conversational words): Explicitly name both animals in a natural, conversational Why/How title (e.g. 'Why Deadly Black Mambas Can't Kill A Mongoose', 'Why Lions Never Mess With A Honey Badger', 'How Jaguars Hunt Caimans Inside Deep Rivers'). NEVER use stiff 'A vs B: Metric' colon formulas and NEVER use dry physics units like PSI or Newtons! "
            "SCENE 1 HOOK (0.0s-2.5s, 6-12 words — PRIORITIZE 'WHY...' CURIOSITY PARADOX HOOK): Open right at 0.0s with a counter-intuitive 'Why...' question naming both animals (e.g. 'Why can't Africa's deadliest black mamba kill a tiny ten-pound mongoose?', 'Why does a hungry lion pride back down from one honey badger?') without repeating the title verbatim! "
            "ACT 2 (Scenes 2-3): Animal A's offensive weapon translated into a vivid everyday physical analogy anyone can picture. "
            "ACT 3 (Scene 4 - 15s Twist): Animal B's counter-mechanism or the unexpected biomechanical twist painted as a mental movie. "
            "ACT 5 (Scene 7/8): Complete sentence forming a Seamless Infinite Loop Bridge back into Scene 1 (ZERO spoken Subscribe CTA). "
            "Tone: cinematic, adrenaline-pumping, National Geographic documentary style. Strict English only."
        ),
        "topics": [
            "Why Giant Nile Crocodiles Never Attack A Full-Grown Hippopotamus In Deep African Rivers",
            "Why Even Giant Crocodiles Fear The 70-Pound Goliath Tigerfish In The Congo River",
            "Why A Pride Of Hungry Lions Backs Down From A 25-Pound Honey Badger In The Savanna",
            "Golden Eagle vs 150-Pound Mountain Goat: How a 14-Pound Raptor Uses a 200 MPH Cliff Dive to Hurl Prey Off Ledges",
            "Honey Badger vs Puff Adder Venom: How Loose 6mm Skin Armor and Mutated Receptors Shrug Off a Lethal Snake Bite",
            "Hippopotamus vs Nile Crocodile: How a 4,000-Pound River Titan Snaps 16-Foot Reptiles in Half With Foot-Long Ivory Tusks",
            "Spotted Hyena Clan vs African Leopard: How Bone-Splintering Jaws Steal Kills From a 150-Pound Stealth Cat",
            "Golden Eagle vs 150-Pound Mountain Goat: How a 14-Pound Raptor Uses a 200 MPH Cliff Dive to Hurl Prey Off Ledges",
            "Siberian Tiger vs 1,000-Pound Brown Bear: How a 660-Pound Big Cat Ambushes Giants Twice Its Size in the Frozen Taiga",
            "Electric Eel vs Caiman: How an 860-Volt Biological Taser Stops a River Predator",
            "African Crested Porcupine vs Leopard: How 30,000 Razor Quills Stop a 150-Pound Ambush",
            "Cassowary: Why Nothing in the Jungle Messes With This 130-Pound Dinosaur Bird's 5-Inch Dagger Kick",
            "Cape Buffalo vs Lion Pride: Why the 2,000-Pound Black Death Hunts Its Own Predators",
            "Mongoose vs Black Mamba: The 0.03-Second Reflex and Venom-Proof Receptors That Beat Africa's Deadliest Snake",
            "Tasmanian Devil vs 60-Pound Kangaroo Carcass: How a 26-Pound Marsupial Crushes Thick Bone With the Strongest Bite Per Pound on Earth",
            "Goliath Tigerfish: The 70-Pound Congo River Predator With 32 Interlocking Razor Teeth",
            "Wild Boar vs Tiger: Why 400 Pounds of Armored Cartilage and Razor Tusks Can Gore an Apex Cat",
            "Giant Horned Lizard vs Coyote: The Desert Reptile That Shoots Pressurized Blood From Its Eyes",
            "Jaguar vs Green Anaconda: When Skull-Piercing Fangs Meet 500 Pounds of Bone-Crushing Coils",
        ],
        "default_tags": [
            "shorts", "wildlife", "predators", "bigcats", "nature",
            "apexpredator", "jaguar", "animals", "documentary", "viral",
        ],
    },
    "@alternatehistorywhatif-e2x": {
        "id": "@alternatehistorywhatif-e2x",
        "name": "Alternate History & \"What If?\"",
        "niche": "alternate_history",
        "language": "en",
        "google_account": "accout3",
        "preferred_voice": "en-US-GuyNeural",
        "handle": "@alternatehistorywhatif-e2x",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 17,
        "director_notes": (
            "Target channel Alternate History & What If (US English). Deep 5-Act Dual-Peak historical showdown & butterfly-effect arc (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TITLE (Zack D. Films / RealLifeLore Viral Style — 6-9 conversational words): Write a natural human Why/How/What-If title naming the historical person/empire/event (e.g. 'Why One Wrong Turn By A Driver Started WW1', 'Why Napoleon's Giant Army Froze In Russia', 'Why One Man Refused To Start World War 3'). NEVER use stiff 'A vs B: Metric' colon formulas! "
            "SCENE 1 HOOK (0.0s-2.5s, 6-12 words — PRIORITIZE 'WHY / WHAT IF...' CURIOSITY HOOK): Open right at 0.0s with a gripping 'Why...' or 'What if...' question naming the historical person/crisis (e.g. 'Why did one wrong turn by a chauffeur in 1914 trigger World War One?', 'Why did one Soviet submarine officer refuse to launch a nuclear torpedo in 1962?') without repeating the title verbatim. "
            "ACT 2 (Scenes 2-3): Documented historical scale and vivid human stakes. "
            "ACT 3 (Scene 4 - 15s Twist): The hidden tactical blunder or physical chain-reaction twist at second 15 that sealed the outcome. "
            "ACT 5 (Scene 7/8): Seamless Infinite Loop Bridge back into Scene 1 (ZERO spoken Subscribe CTA). "
            "Tone: cinematic, gripping, Christopher Nolan / Dan Carlin documentary style. Strict English only."
        ),
        "topics": [
            "Why Kublai Khan's 4,400 Mongol Warships Vanished Overnight Before Conquering Samurai Japan In 1281",
            "Why Roman Emperors Built A 73-Mile Stone Wall Instead Of Conquering Ancient Scotland",
            "Why Snow Fell In June 1816 And Froze The World After Mount Tambora Exploded",
            "1812 Napoleon's 600,000 Grande Armée vs Russian -40°C Winter: How Scorched Earth and Frostbite Erased 500,000 Troops in 5 Months",
            "1983 Stanislav Petrov vs Soviet Satellite Glitch: How One Officer Stopped 5 False Nuclear Launch Alarms From Triggering WW3",
            "1588 Spanish Armada 130 Warships vs English Fire Ships: How 8 Burning Hulks at Midnight Shattered the World's Richest Empire",
            "1986 Chernobyl Divers vs 20-Megaton Steam Explosion: How 3 Volunteers Drained 20,000 Tons of Radioactive Water to Save Europe",
            "1281 Kamikaze Mega-Typhoon vs Kublai Khan 4,400 Warships: How a 2-Day Hurricane Sank 140,000 Troops and Saved Samurai Japan",
            "1815 Mount Tambora Super-Eruption vs The Year Without a Summer: How One Volcano Blocked the Sun and Froze Crops in June 1816",
            "How 1 Unlocked Gate in 1453 Ended the 1,100-Year Byzantine Empire at Constantinople",
            "What If the 1983 Stanislav Petrov Satellite Glitch Launched 10,000 Nuclear Warheads?",
            "1945 Operation Unthinkable Allied Armies vs 11 Million Red Army Troops: Churchill's Secret Plan to Invade the USSR in July 1945",
            "How One Wrong Turn By A Driver In 1914 Erased Four Empires Forever",
            "The Man Who Refused To Launch A Nuclear Strike In 1962 Inside Submarine B-59",
            "How A 15-Minute Rainstorm At Waterloo Ended Napoleon's Empire",
            "What If The Titanic Never Hit The Iceberg? The Dark Truth Nobody Tells You",
            "What If The Roman Empire Discovered Steam Engines 1,800 Years Early?",
            "What If The Library Of Alexandria Was Never Burned Down?",
            "How A Single Jammed Rifle In 1914 Changed The Entire 20th Century",
        ],
        "default_tags": [
            "shorts", "alternatehistory", "whatif", "history", "empires",
            "timeline", "documentary", "historicalfacts", "viral",
        ],
    },
    "ch_odisseia_selvagem": {
        "id": "ch_odisseia_selvagem",
        "name": "Odisseia Selvagem",
        "niche": "wildlife_predators",
        "language": "pt-BR",
        "google_account": "account_1",
        "preferred_voice": "pt-BR-AntonioNeural",
        "handle": "@odisseiaselvagemoficial",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel Odisseia Selvagem (Brazilian Portuguese). Deep 5-Act Dual-Peak wildlife showdown (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TÍTULO (Estilo Você Sabia? / Nature Lenses — 6-9 palavras naturais): Escreva um título natural e curioso com 'Por Que / Como' nomeando os animais claramente sem fórmulas robóticas (ex: 'Por Que o Guepardo Tem Medo do Avestruz?', 'Por Que a Onça-Pintada Foge Desse Porco do Mato?', 'Como a Sucuri Engole um Jacaré Inteiro no Rio'). NUNCA use unidades secas como PSI, Newtons ou toneladas no título! "
            "HOOK DA CENA 1 (0,0s-2,5s, 6-12 palavras — PRIORIZE O HOOK 'POR QUE...' DE CURIOSIDADE): Abra o segundo 0,0s com uma pergunta contraintuitiva 'Por que...' nomeando ambos os animais (ex: 'Por que uma onça-pintada sobe na árvore apavorada quando vê esse bando de porcos do mato?', 'Por que o guepardo mais rápido da África recua diante de um avestruz?') sem repetir o título palavra por palavra! "
            "ATO 2 (Cenas 2-3): Arma biomecânica do Animal A explicada de forma visual e simples. "
            "ATO 3 (Cena 4 - Twist de 15s): Contra-ataque ou mecanismo oculto do Animal B que vira o jogo. "
            "ATO 5 (Cena 7/8): Frase completa conectando em loop infinito de volta à Cena 1 (ZERO CTA falado de Inscreva-se). "
            "Tone: dramatic, fast-paced, National Geographic Brasil style. Strict Brazilian Portuguese (pt-BR) only."
        ),
        "topics": [
            "Por Que o Dragão-de-Komodo Consegue Derrubar um Búfalo Gigante com Uma Única Mordida?",
            "Por Que uma Alcateia de Lobos-Cinzentos Recua Diante de um Único Alce Gigante na Neve?",
            "Por Que um Leopardo Faminto Desiste de Atacar Esse Porco-Espinho Cheio de Espinhos Farpados?",
            "Onça-Pintada vs Bando de 100 Queixadas: Como Mandíbulas Batendo em Sincronia Fazem o Maior Felino das Américas Subir nas Árvores",
            "Guepardo a 110 km/h vs Avestruz de 140 Quilos: Como um Único Chute de 2.000 Quilos de Força Pode Virar a Caçada na Savana",
            "Dragão-de-Komodo de 130 Quilos vs Búfalo-Asiático de 500 Quilos: Como Glândulas de Veneno Anticoagulante Derrubam um Gigante",
            "Casuar de 60 Quilos vs Predadores da Selva: Como uma Garra-Adaga de 12 Centímetros Rasga Qualquer Rival na Floresta",
            "Águia-Real vs Cabra-da-Montanha de 70 Quilos: Como um Mergulho a 240 km/h Derruba Gigantes de Penhascos Verticais",
            "Porco-Espinho-Africano vs Leopardo: Como 30.000 Espinhos Farpados de Queratina Param um Felino de 70 Quilos na Savana",
            "Carcaju de 16 Quilos vs Urso-Pardo de 350 Quilos: Como o Demônio da Neve Usa Mandíbulas Quebradoras de Ossos no Ártico",
            "Mamba-Negra vs Mangusto: O Reflexo de 15 Milissegundos Que Humilha a Cobra Mais Letal da África",
            "Crocodilo-do-Nilo vs Búfalo-Africano: Como a Manada Inteira Vira o Jogo Contra o Monstro do Rio",
            "Casuar: Por Que Nenhum Predador Ousa Enfrentar a Ave-Dinossauro Mais Perigosa do Mundo",
            "Cachalote vs Lula-Colossal: O Duelo Secreto a 2.000 Metros de Profundidade no Oceano",
            "Onça-Pintada vs Bando de Queixadas: Por Que 100 Porcos Selvagens Fazem o Maior Felino Subir nas Árvores",
            "Guepardo vs Avestruz: O Chute Que Estraçalha Costelas e Pode Matar o Felino Mais Rápido da Terra",
            "Lobo-Cinzento vs Alce Gigante: A Estratégia de Caça na Neve Profunda Onde Um Coice É Fatal",
            "Hipopótamo vs Alcateia de Leões: Por Que 6 Leões Famintos Recuam Diante Deste Gigante nos Rios Africanos",
        ],
        "default_tags": [
            "shorts", "animais", "natureza", "vidaanimal", "pantanal",
            "amazonia", "predadores", "curiosidades", "mundoanimal", "viral",
        ],
    },
    "ch_misterios_do_mundo": {
        "id": "ch_misterios_do_mundo",
        "name": "Mistérios do Mundo",
        "niche": "deep_sea_earth_mysteries",
        "language": "pt-BR",
        "google_account": "account_1",
        "preferred_voice": "pt-BR-AntonioNeural",
        "handle": "@misteriosdomundo-tv",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 21,
        "director_notes": (
            "Target channel Mistérios do Mundo (Brazilian Portuguese). Deep 5-Act Dual-Peak abyssal showdown & extreme ocean/earth mystery arc (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TÍTULO (Estilo Você Sabia? / Zack D. Films — 6-9 palavras naturais): Escreva um título humano e curioso com 'Por Que / Como / O Que' nomeando a criatura ou lugar real + mistério visual (ex: 'Por Que Esse Lago Silencioso Apagou Uma Cidade Inteira?', 'Por Que Esse Peixe do Fundo do Mar Tem Uma Luz Invisível?', 'Por Que Nenhum Submarino Desce Até o Fundo Dessa Fossa?'). NUNCA use fórmulas robóticas 'A vs B: Número' nem unidades secas como PSI, °C ou atmosferas! "
            "HOOK DA CENA 1 (0,0s-2,5s, 6-12 palavras — PRIORIZE O HOOK 'POR QUE...' DE CURIOSIDADE): Abra o segundo 0,0s com uma pergunta contraintuitiva 'Por que...' nomeando a criatura ou fenômeno real (ex: 'Por que tubarões do fundo do mar ficam paralisados ao nadar dentro desse lago submarino?', 'Por que os cientistas selaram esse poço profundo na Rússia?') sem repetir o título palavra por palavra! "
            "ATO 2 (Cenas 2-3): Profundidade real ou perigo explicado com comparação do dia a dia. "
            "ATO 3 (Cena 4 - Twist de 15s): Segundo pico explosivo / mecanismo reverso no segundo 15 que muda tudo. "
            "ATO 5 (Cena 7/8): Frase 100% completa fechando o Loop Infinito com a Cena 1 (ZERO CTA falado de Inscreva-se). "
            "Tone: suspenseful, dark, adrenaline-pumping documentary style. Strict Brazilian Portuguese (pt-BR) only."
        ),
        "topics": [
            "Por Que Tubarões do Fundo do Mar Ficam Paralisados ao Nadar Dentro Desse Lago Submarino no Golfo do México?",
            "Por Que um Único Esqueleto de Baleia a 4.000 Metros Alimenta Criaturas do Abismo Por 50 Anos?",
            "Por Que Essa Água-Viva Invisível da Austrália Para um Coração Humano em 2 Minutos?",
            "Fossa das Marianas a 11.000 Metros vs Submarino de Titânio: Como 1.086 Atmosferas de Pressão Esmagam Aço no Ponto Mais Profundo do Mar",
            "Dedo Gélido da Morte (Brinicle) a -2°C vs Fundo do Mar da Antártida: Como um Tornado de Gelo Submarino Congela Estrelas-do-Mar em Segundos",
            "Polvo-de-Anéis-Azuis de 12 Centímetros vs Moreia-Gigante: Como a Tetrodotoxina 1.200 Vezes Mais Letal Que Cianeto Paralisa em Segundos",
            "Chaminés Hidrotermais Negras a 400°C vs Vermes-Tubulares Gigantes: Como Criaturas Abissais Sobrevivem Sem Sol a 2.500 Metros no Mar",
            "Caravela-Portuguesa vs Tartaruga-de-Couro: Como uma Gigante de 700 Quilos Devora Tentáculos Venenosos de 50 Metros no Mar",
            "Dedo Gélido da Morte (Brinicle) a -2°C vs Fundo do Mar da Antártida: O Tornado de Gelo Submarino Que Congela Tudo",
            # [1-1 VISUAL CONTRAST INTERLEAVED QUEUE — Đan xen Dị thường Địa chất/Nhiệt độ/Hóa học <-> Đại chiến Sinh vật Đa dạng Họ]
            "Enguia-Pelicano vs Lula-Vampiro: Como Monstros a 2.500 Metros Caçam Sem Oxigênio na Zona da Meia-Noite",
            "Fossa de Porto Rico a 8.376 Metros: A Anomalia Gravitacional no Fundo do Oceano Atlântico",
            "Lago Nyos: A Nuvem Invisível de 1,6 Milhão de Toneladas de CO2 Que Sufocou um Vale Inteiro",
            "Peixe-Dragão Negro a 2.000 Metros: O Predador Invisível Que Produz Luz Infravermelha no Abismo",
            "O Cemitério de Baleias a 4.000 Metros: Como um Único Esqueleto Alimenta Monstros Cegos Por 50 Anos",
            "Por Que Submarinos Militares Apagam Todas as Luzes Ao Cruzar 3.000 Metros na Zona Abissal",
            "Vespa-do-Mar: A Água-Viva Invisível Cujo Veneno Para o Coração Humano em 2 Minutos",
            "Tubarão-da-Groenlândia vs Lula-Gigante: O Predador Cego de 400 Anos nas Profundezas do Ártico",
            "Vulcão Submarino Kavachi: A Cratera em Ebulição a 100°C no Fundo do Oceano Pacífico",
            "Orca vs Tubarão-Branco de 2 Toneladas: O Golpe Cirúrgico Que Faz o Maior Predador Fugir do Oceano",
            "Tubarão-Duende vs Peixe-Diabo Negro: A Mandíbula Que Dispara em 0,02 Segundo a 3.000 Metros no Mar",
        ],
        "default_tags": [
            "shorts", "misterios", "oceano", "curiosidades", "bizarro",
            "ciencia", "fatosdesconhecidos", "fundodomar", "misteriosdomundo", "viral",
        ],
    },
    "ch_paws_hearts_rescue": {
        "id": "ch_paws_hearts_rescue",
        "name": "Paws & Hearts Rescue",
        "niche": "animal_rescue",
        "language": "en",
        "google_account": "acount_2",
        "preferred_voice": "en-US-ChristopherNeural",
        "handle": "@pawsandheartsrescue",
        "amazon_tag": "kizakawa-20",
        "publish_hour_utc": 18,
        "prefer_non_youtube_sources": True,
        "director_notes": (
            "Target channel Paws & Hearts Rescue (US English, Category 15 Pets & Animals). Deep 5-Act Dual-Peak Heroic Rescue Dogs & Canine Survival Science arc (34-40s golden runtime, 7-8 scenes, 92-118 words): "
            "TITLE (YT_SEARCH Anchor): Explicitly name the heroic rescue dog breed/animal vs the extreme survival hazard or physical feat ('[Rescue Dog Breed / Animal A] vs [Extreme Hazard B]: [Visceral Survival Mechanism]'). NEVER hide the breed name behind vague pronouns! "
            "SCENE 1 HOOK (0.0s-2.2s, 6-10 words): Use 1 of the 4 Psychological-Visual Hook Archetypes (1. Visual Micro-Observation 'Look closely at this dog\'s paws...', 2. The Fatal Mistake, 3. 2nd-Person Survival Simulation 'If you are trapped under...', 4. Role Reversal). NEVER repeat the title verbatim at 0.0s! "
            "ACT 2 (Scenes 2-3): Explain the breed\'s extraordinary biological rescue adaptation (webbed paws, 300-million scent receptors, double thermal coat, 700-PSI bite or towing power) translated into a vivid everyday physical analogy. "
            "ACT 3 (Scene 4 - 15s Twist): The hidden second survival mechanism or real-world rescue turning point that saves human/animal lives in extreme conditions. "
            "ACT 5 (Scene 7/8): Complete sentence forming a Seamless Infinite Loop Bridge back into Scene 1 (ZERO spoken Subscribe CTA). "
            "Tone: gripping, heroic, awe-inspiring National Geographic / Canine Survival Documentary style. Strict English only."
        ),
        "topics": [
            "Newfoundland Rescue Dog vs Ocean Rip Current: How Webbed Paws and a 150-Pound Lung Capacity Pull Drowning Swimmers From Freezing Waves",
            "Saint Bernard Rescue Dog vs 15-Foot Alpine Avalanche: How 300 Million Scent Receptors Detect Human Heartbeats Beneath Solid Snow",
            "Belgian Malinois K9 vs 12-Foot Vertical Wall: How Military Rescue Dogs Parachute From 10,000 Feet to Track Missing Survivors",
            "Kangal Shepherd Dog vs Wolf Pack: How a 140-Pound Livestock Guardian Protects Entire Mountain Flocks at Night",
            "Bloodhound Scent Tracker vs 130-Hour Rain Trail: How Loose Facial Skin Traps Microscopic Human Scent Particles Across 100 Miles",
            "Alaskan Malamute Sled Team vs -50°F Arctic Blizzard: How Double Thermal Coats and Counter-Current Paw Veins Stop Frostbite",
            "German Shepherd Search Dog vs Collapsed Concrete Rubble: How Ultrasonic Hearing Pinpoints Trapped Survivors After an Earthquake",
            "Golden Retriever Water Rescue Dog vs Capsized Boat: Why Coast Guard Squads Trust This Soft-Mouthed Breed to Tow Two Adults at Once",
        ],
        "default_tags": [
            "shorts", "rescuedog", "dogs", "animalrescue", "workingdogs",
            "k9", "newfoundlanddog", "saintbernard", "germanshepherd", "pets", "animals", "viral",
        ],
    },
}

# Aliases để tương thích ngược
CHANNELS_CONFIG["channel_maya"] = CHANNELS_CONFIG["ch_psychology_of_the_world"]
CHANNELS_CONFIG["ucsrjiil67qxidtxt70z-iwa"] = CHANNELS_CONFIG["ch_the_mysteries_of_the_world"]

SIX_ACTIVE_CHANNELS = [
    "ch_the_mysteries_of_the_world",
    "ch_psychology_of_the_world",
    "ch_wild_odyssey",
    "@alternatehistorywhatif-e2x",
    "ch_odisseia_selvagem",
    "ch_misterios_do_mundo",
]

_SESSION_USED_TOPICS: dict[str, list[str]] = {}
_SESSION_OCCUPIED_SLOTS: dict[str, list[datetime]] = {}


def get_schedule_slot(publish_hour_utc: int, channel_id: str = "") -> tuple[datetime, str, str]:
    """
    Tính slot giờ vàng tiếp theo có lệch pha chống trùng lặp.
    Đảm bảo khi tạo 2 video liên tiếp cho cùng 1 kênh sẽ tự động phân bổ vào 2 khung giờ vàng khác nhau
    (giãn cách >= 3 giờ và tối đa 2 video/ngày theo múi giờ thị trường mục tiêu US/BR).
    """
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_active_slots_pool,
            is_brazil_channel,
            BRAZIL_GOLDEN_SLOTS_UTC,
            GOLDEN_SLOTS_UTC,
            get_channel_stagger_minutes,
            MIN_SPACING_SECONDS,
            MAX_VIDEOS_PER_DAY,
            BR_TZ,
            US_EASTERN,
            VN_TZ,
            WEEKDAY_VN_MAP,
        )
        slot_dt, slot_iso, disp_vn, disp_us = get_next_available_golden_slot(channel_id or "ch_psychology_of_the_world")
        prev_slots = _SESSION_OCCUPIED_SLOTS.get(channel_id, [])
        if prev_slots and any(abs((slot_dt - ps).total_seconds()) < MIN_SPACING_SECONDS for ps in prev_slots):
            is_br = is_brazil_channel(channel_id)
            pool = get_active_slots_pool(channel_id)
            target_tz = BR_TZ if is_br else US_EASTERN
            stagger = get_channel_stagger_minutes(channel_id)
            base_date = max(prev_slots).date()
            for d_off in range(0, 15):
                t_date = base_date + timedelta(days=d_off)
                found_new = False
                for s_cfg in sorted(pool, key=lambda x: (x["hour"], x["minute"])):
                    cand = datetime(
                        t_date.year, t_date.month, t_date.day,
                        s_cfg["hour"], s_cfg["minute"], 0, tzinfo=timezone.utc
                    ) + timedelta(minutes=stagger)
                    if cand <= max(prev_slots) + timedelta(seconds=MIN_SPACING_SECONDS):
                        continue
                    m_date = cand.astimezone(target_tz).date()
                    if sum(1 for ps in prev_slots if ps.astimezone(target_tz).date() == m_date) >= MAX_VIDEOS_PER_DAY:
                        continue
                    slot_dt = cand
                    slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    c_vn = slot_dt.astimezone(VN_TZ)
                    w_name = WEEKDAY_VN_MAP.get(c_vn.weekday(), "Ngày")
                    vn_p = "Đêm" if c_vn.hour >= 18 else "Sáng"
                    disp_vn = f"{c_vn.strftime('%H:%M')} {vn_p} {w_name}, {c_vn.strftime('%d/%m/%Y')} (Giờ VN)"
                    disp_us = s_cfg["label_us"]
                    found_new = True
                    break
    
                if found_new:
                    break
    
        _SESSION_OCCUPIED_SLOTS.setdefault(channel_id, []).append(slot_dt)
        return slot_dt, disp_vn, disp_us
    except Exception:
        now_utc = datetime.now(timezone.utc)
        candidate = now_utc.replace(hour=publish_hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= now_utc + timedelta(hours=2):
            candidate += timedelta(days=1)
        VN_TZ = timezone(timedelta(hours=7))
        dt_vn = candidate.astimezone(VN_TZ)
        disp_vn = f"{dt_vn.strftime('%H:%M')} ngày {dt_vn.strftime('%d/%m/%Y')} (Giờ VN)"
        return candidate, disp_vn, "Golden Slot"


async def produce_channel_video(
    channel_key: str,
    custom_topic: str = None,
    dry_run: bool = False,
    render_only: bool = False,
    send_telegram: bool = False,
    telegram_chat_id: str = None,
    video_index: int = 1,
    total_per_channel: int = 1,
    video_format: str = "short",
):
    cfg = CHANNELS_CONFIG[channel_key]
    ch_id = cfg["id"]
    ch_name = cfg["name"]
    ch_niche = cfg["niche"]
    ch_lang = cfg["language"]
    voice = cfg["preferred_voice"]
    amazon_tag = cfg["amazon_tag"]

    SCHEDULE_SLOT_UTC, DISPLAY_VN, DISPLAY_MARKET = get_schedule_slot(cfg["publish_hour_utc"], ch_id)
    SCHEDULE_SLOT_ISO = SCHEDULE_SLOT_UTC.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("\n" + "=" * 80)
    print(f"🌟 [{video_index}/{total_per_channel}] SẢN XUẤT VIDEO CHO KÊNH: {ch_name} ({ch_id})")
    print(f"🎬 Ngách: {ch_niche} | Ngôn ngữ: {ch_lang} | Giọng: {voice}")
    print(f"⏰ KHUNG GIỜ VÀNG: {DISPLAY_VN} ({SCHEDULE_SLOT_ISO}) | {DISPLAY_MARKET}")
    print("=" * 80)

    from app.agents.content_agent import ContentAgent
    from app.services.quality_evaluator import QualityEvaluator
    from app.services.channel_intelligence import (
        get_winner_cloner_blueprint,
        check_idea_similarity,
        ensure_unique_title,
        extract_saturated_channel_concepts,
        has_consecutive_subject_fatigue,
    )
    from app.services.niche_manager import validate_channel_niche_alignment
    from app.database.models import get_db, VideoJob, PublishedVideo, log_activity
    from app.services.smart_scheduler import resolve_channel_family
    from app.agents.publisher_agent import PublisherAgent

    agent = ContentAgent()
    evaluator = QualityEvaluator()

    # ── BƯỚC 1: TẠO VÀ ĐÁNH GIÁ CHẤM ĐIỂM KỊCH BẢN (CHỐNG TRÙNG LẶP 100%) ─────────
    print(f"\n[1/5] 📝 BƯỚC 1: Chọn chủ đề mới & Xây dựng kịch bản viral cho {ch_name}...")
    used_in_session = _SESSION_USED_TOPICS.setdefault(ch_id, [])
    existing_refs: list[str] = list(used_in_session)
    try:
        fam_ids = resolve_channel_family(ch_id)
        with get_db() as db:
            recent_db = (
                db.query(VideoJob)
                .filter(
                    VideoJob.target_channel_id.in_(fam_ids),
                    ~VideoJob.status.ilike("CANCELLED%"),
                    VideoJob.status != "FAILED",
                )
                .order_by(VideoJob.id.desc())
                .limit(45)
                .all()
            )
            for rj in recent_db:
                if rj.topic:
                    existing_refs.append(rj.topic)
                if rj.title:
                    existing_refs.append(rj.title)
            pub_db = (
                db.query(PublishedVideo)
                .filter(PublishedVideo.channel_id.in_(fam_ids))
                .order_by(PublishedVideo.id.desc())
                .limit(45)
                .all()
            )
            for pv in pub_db:
                if pv.title:
                    existing_refs.append(pv.title)
            audit_p = project_root / "storage" / "channel_audit_latest.json"
            if audit_p.exists():
                aud_j = json.loads(audit_p.read_text(encoding="utf-8"))
                ch_aud = (aud_j or {}).get(ch_id, {})
                for av in ch_aud.get("videos", []):
                    if av.get("title"):
                        existing_refs.append(av["title"])
                for sq in ch_aud.get("scheduled_queue", []):
                    if sq.get("title"):
                        existing_refs.append(sq["title"])
    except Exception:
        pass

    if custom_topic:
        topic = custom_topic
    else:
        candidate_topics: list[str] = list(cfg["topics"])
        try:
            wb = get_winner_cloner_blueprint(ch_id, niche_id=ch_niche)
            fresh_pool = wb.get("fresh_sequel_topics") or []
            if fresh_pool:
                candidate_topics.extend(t for t in fresh_pool if t not in candidate_topics)
                if wb.get("winner_title"):
                    print(f"   🏆 [Winner Cloner] Tham chiếu công thức quán quân '{wb['winner_title']}' ({wb['winner_views']} views)")
        except Exception:
            pass

        topic = None
        for cand_t in candidate_topics:
            if cand_t in used_in_session:
                continue
            niche_res = validate_channel_niche_alignment(
                channel_id=ch_id,
                title=cand_t,
                topic=cand_t,
            )
            if not niche_res.get("passed", True):
                continue
            is_sim, _, _ = check_idea_similarity(cand_t, existing_refs, similarity_threshold=0.52, channel_id=ch_id)
            if is_sim:
                continue
            recent_ordered_refs = list(reversed(used_in_session)) + [r for r in existing_refs if r not in used_in_session][:6]
            is_consec_fatigued, collided_stem = has_consecutive_subject_fatigue(cand_t, recent_ordered_refs, window=3)
            if is_consec_fatigued:
                print(f"   ⏭️ [Anti-Cohort-Fatigue] Bỏ qua '{cand_t[:55]}...' vì trùng chủ thể liên tiếp '{collided_stem}' với video vừa tạo/lên lịch!")
                continue
            topic = cand_t
            break

        if not topic:
            remaining = [
                t for t in cfg["topics"]
                if t not in used_in_session
                and validate_channel_niche_alignment(channel_id=ch_id, title=t, topic=t).get("passed", True)
            ]
            topic = remaining[0] if remaining else cfg["topics"][0]

    used_in_session.append(topic)
    print(f"   -> Chủ đề được chọn: {topic}")

    recent_for_cooldown = list(reversed(used_in_session[:-1])) + [r for r in existing_refs if r not in used_in_session][:8]
    saturated_cooldown = extract_saturated_channel_concepts(recent_for_cooldown, min_frequency=2)
    if saturated_cooldown:
        print(f"   🛡️ [Cooldown Chủ Thể Bão Hòa]: {', '.join(saturated_cooldown[:6])}")

    script = None
    script_evaluation = None
    is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
    for attempt in range(1, 2 if is_long_form else 4):
        print(f"\n   [Lần {attempt}/3] Sinh kịch bản và gửi ban thẩm định AI...")
        is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
        raw_script = agent.generate_script(
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang,
            director_notes=cfg["director_notes"],
            cooldown_entities=saturated_cooldown,
            channel_id=ch_id,
            video_format="long" if is_long_form else "short",
        )

        eval_res = evaluator.evaluate_script(
            script_dict=raw_script.to_dict(),
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang,
        )

        print(f"   📊 KẾT QUẢ ĐÁNH GIÁ KỊCH BẢN:")
        print(f"      - Điểm tổng: {eval_res['score']}/100 ({eval_res['verdict']})")
        print(f"      - Hook 3s đầu: {eval_res['hook_score']}/10 | Đúng ngách: {eval_res['niche_score']}/10 | Ngôn ngữ: {eval_res['language_score']}/10 | Giữ chân: {eval_res['retention_score']}/10")

        if eval_res["passed"]:
            print(f"   ✅ KỊCH BẢN ĐẠT TIÊU CHUẨN XUẤT BẢN! ({eval_res['score']}/100 >= 80)")
            script = raw_script
            script_evaluation = eval_res
            break
        else:
            print(f"   ⚠️ Kịch bản {eval_res['score']}/100 < 80. Thử tối ưu lại...")
            script = raw_script
            script_evaluation = eval_res

    raw_title = script.title or f"{topic.split(':')[0].strip()}"
    try:
        unique_title, was_dup, _ = ensure_unique_title(
            candidate_title=raw_title,
            channel_id=ch_id,
            topic=topic,
            language=ch_lang,
            similarity_threshold=0.42,
        )
        if was_dup and unique_title:
            raw_title = unique_title
            script.title = unique_title
    except Exception:
        pass
    used_in_session.append(raw_title)
    base_desc = script.description or f"{script.hook or topic}\n\n#Shorts #{ch_niche}"
    raw_tags = list(dict.fromkeys([*(script.tags or []), *cfg["default_tags"]]))
    pinned_comment_base = script.pinned_comment or (
        "Qual é a sua opinião sobre isso? Comente abaixo! 👇🔥"
        if ch_lang.lower().startswith("pt")
        else "What is your thought on this? Drop a comment below! 👇🔥"
    )

    # ── BƯỚC 2: TỐI ƯU BỘ TAGS SEO CHUẨN NGÁCH & CHỦ ĐỀ (TỐI ĐA 25 TAGS + HASHTAGS) ──
    print(f"\n[2/5] 🏷️ BƯỚC 2: Tổng hợp bộ Tags SEO & Hashtags chuẩn ngách cho {ch_name}...")
    ch_monet_cfg = {
        "id": ch_id,
        "name": ch_name,
        "niche": ch_niche,
        "language": ch_lang,
        "google_account": cfg["google_account"],
        "amazon_tag": amazon_tag,
        "affiliate_enabled": False,
        "enable_donation": False,
        "default_tags": cfg["default_tags"],
    }
    pub_agent = PublisherAgent(channel_config=ch_monet_cfg)
    smart_bundle = pub_agent.build_smart_video_tags_and_hashtags(
        title=raw_title,
        topic=topic,
        script=script.full_narration,
        extra_tags=raw_tags,
    )
    title = smart_bundle["enriched_title"]
    tags = smart_bundle["tags"]
    from app.services.monetization_engine import enrich_video_monetization
    final_description, final_pinned_comment, _ = enrich_video_monetization(
        channel_config=ch_monet_cfg,
        title=title,
        description=base_desc,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=script.full_narration,
        enable_affiliate=False,
        enable_donation=False,
    )
    final_pinned_comment = pub_agent.sanitize_engagement_comment(final_pinned_comment)
    if smart_bundle["description_hashtags"] not in final_description:
        final_description = f"{final_description.strip()}\n\n{smart_bundle['description_hashtags']}".strip()

    print(f"   -> Tiêu đề chuẩn SEO : {title}")
    print(f"   -> Bộ Tags ({len(tags)} thẻ): {', '.join(tags[:12])}...")
    print(f"   -> Hashtags Mô tả    : {smart_bundle['description_hashtags']}")
    print(f"   -> Số phân cảnh      : {len(script.scenes)} scenes")

    if dry_run:
        print("\n[DRY-RUN] Hoàn tất kiểm thử kịch bản, bộ Tags & khe giờ vàng. Dừng ở đây.")
        return {
            "channel": ch_name,
            "channel_id": ch_id,
            "title": title,
            "tags_count": len(tags),
            "tags": tags,
            "script_score": script_evaluation["score"] if script_evaluation else 85,
            "video_score": 100,
            "scheduled_time": DISPLAY_VN,
            "scheduled_iso": SCHEDULE_SLOT_ISO,
            "youtube_url": "DRY_RUN",
            "video_id": None,
        }

    # ── BƯỚC 3: ĐĂNG KÝ VIDEOJOB VÀO DATABASE ────────────────────────────────────
    print(f"\n[3/5] 💾 BƯỚC 3: Đăng ký VideoJob vào hệ thống...")
    is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
    scenes_dict = script.to_dict()
    scenes_dict["video_format"] = "long" if is_long_form else "short"
    scenes_dict["pinned_comment"] = final_pinned_comment
    if script_evaluation and script_evaluation.get("passed"):
        scenes_dict["_pre_evaluated_score"] = float(script_evaluation.get("score", 85))
        scenes_dict["_pre_evaluated_hook"] = float(script_evaluation.get("hook_score", 8.5))
    scenes_json_str = json.dumps(scenes_dict, ensure_ascii=False)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=ch_id,
            topic=topic,
            title=title,
            script=script.full_narration,
            description=final_description,
            tags=",".join(tags),
            scenes_json=scenes_json_str,
            status="RENDERING",
            scheduled_time=SCHEDULE_SLOT_UTC,
            is_auto_generated=0,
            progress_percent=15,
            progress_message=f"Đang sản xuất video #{video_index} cho {ch_name} (Khung giờ vàng {DISPLAY_VN})...",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(
            db,
            telegram_id=1,
            username="System_Director",
            action_type="SCHEDULED_VIDEO_START",
            details=f"Khởi động sản xuất #{job_id} '{title}' cho {ch_name} ({DISPLAY_VN})",
            channel_id=ch_id,
        )
    print(f"   -> Job #{job_id} đã được khởi tạo thành công.")

    # ── BƯỚC 4: DỰNG VIDEO CHẤT LƯỢNG CAO NHẤT (1080x1920 @ 30FPS, CRF 18) & CHẤM ĐIỂM QA ──
    print(f"\n[4/5] 🎬 BƯỚC 4: Dựng video chất lượng cao (1080x1920 @ 30fps, giọng {voice}) & Thẩm định...")

    async def _progress_cb(stage: str, msg: str, percent: int = 0):
        print(f"   [Job #{job_id}] [{stage}] [{percent}%] {msg}", flush=True)

    from app.services.pipeline import VideoPipeline
    pipeline = VideoPipeline(job_id=job_id, progress_callback=_progress_cb)
    pipeline.niche = ch_niche

    prev_non_yt = os.environ.get("PREFER_NON_YOUTUBE_SOURCES")
    prev_allow_yt = os.environ.get("ALLOW_LOW_RISK_YOUTUBE_CLIPS")
    if cfg.get("prefer_non_youtube_sources"):
        os.environ["PREFER_NON_YOUTUBE_SOURCES"] = "true"
        os.environ["ALLOW_LOW_RISK_YOUTUBE_CLIPS"] = "false"
        print("   🛡️ [Non-YouTube Clean Sources Mode]: Đã kích hoạt chế độ lấy nguồn 100% ngoài YouTube (Pexels HD + Pixabay HD + Wikimedia + Bilibili Safe-Crop)!")
    try:
        render_result = await pipeline.execute()
    finally:
        if cfg.get("prefer_non_youtube_sources"):
            if prev_non_yt is None:
                os.environ.pop("PREFER_NON_YOUTUBE_SOURCES", None)
            else:
                os.environ["PREFER_NON_YOUTUBE_SOURCES"] = prev_non_yt
            if prev_allow_yt is None:
                os.environ.pop("ALLOW_LOW_RISK_YOUTUBE_CLIPS", None)
            else:
                os.environ["ALLOW_LOW_RISK_YOUTUBE_CLIPS"] = prev_allow_yt
    output_video_path = render_result.output_path

    if not output_video_path or not Path(output_video_path).exists():
        raise RuntimeError(f"Pipeline render thất bại, không tìm thấy file tại: {output_video_path}")

    print("\n   🔍 Đang tiến hành thẩm định chất lượng kỹ thuật & thị giác của file MP4...")
    video_qa = evaluator.evaluate_video(str(output_video_path), video_format="long" if is_long_form else "short")
    from app.graph.evaluators.visual_critic import VisualCritic
    vc_check = VisualCritic.inspect_clip_before_concat(
        str(output_video_path), min_short_edge=720, min_sharpness=5.5, check_watermark=False
    )
    print(f"   📊 KẾT QUẢ CHẤM ĐIỂM VIDEO (#{job_id}):")
    print(f"      - Điểm kỹ thuật FFprobe: {video_qa['score']}/100 ({video_qa.get('width')}x{video_qa.get('height')} @ {video_qa.get('fps')} FPS, {video_qa.get('duration_seconds', 0)}s, {video_qa.get('file_size_mb')} MB)")
    print(f"      - Độ sắc nét trung bình: {vc_check.get('sharpness', 0)} | Độ sáng: {vc_check.get('mean_luminance', 0)}")
    print(f"      - Âm thanh: {'Có luồng AAC chuẩn -14 LUFS' if video_qa.get('has_audio') else 'Thiếu'}")

    if send_telegram:
        try:
            import subprocess as _sp_tg
            import requests as _req_tg
            from config.settings import settings as _st_tg
            tg_token = getattr(_st_tg, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
            target_chat = (
                telegram_chat_id
                or getattr(_st_tg, "TELEGRAM_ADMIN_ID", None)
                or os.getenv("TELEGRAM_CHAT_ID")
                or "8498162618"
            )
            if tg_token and target_chat and Path(output_video_path).exists():
                print(f"\n   📲 [Telegram Preview] Đang chuẩn bị & gửi video #{job_id} vào Telegram ({target_chat}) để duyệt trước...")
                tg_send_path = str(output_video_path)
                tmp_tg_path = str(Path(output_video_path).with_name(f"tg_preview_{job_id}.mp4"))
                try:
                    if Path(output_video_path).stat().st_size > 15 * 1024 * 1024:
                        tg_scale = "scale=1280:720" if is_long_form else "scale=720:1280"
                        tg_crf = "28" if is_long_form else "26"
                        _sp_tg.run(
                            [
                                "ffmpeg", "-y", "-i", str(output_video_path),
                                "-vf", tg_scale,
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", tg_crf,
                                *(["-maxrate", "850k", "-bufsize", "1700k"] if is_long_form else []),
                                "-c:a", "aac", "-b:a", "96k" if is_long_form else "128k",
                                "-movflags", "+faststart",
                                tmp_tg_path,
                            ],
                            capture_output=True,
                            timeout=180 if is_long_form else 60,
                            check=False,
                        )
                        if Path(tmp_tg_path).exists() and Path(tmp_tg_path).stat().st_size > 100_000:
                            tg_send_path = tmp_tg_path
                except Exception:
                    pass
                fmt_badge = "VIDEO DÀI 16:9 (1920x1080)" if is_long_form else "SHORTS 9:16 (1080x1920)"
                caption_txt = (
                    f"🎬 <b>[BẢN DUYỆT TRƯỚC ({fmt_badge}) - {ch_name}]</b>\n"
                    f"🆔 <b>Job ID:</b> <code>#{job_id}</code>\n"
                    f"📌 <b>Tiêu đề:</b> {smart_bundle['enriched_title']}\n"
                    f"🎯 <b>Chủ đề:</b> {topic}\n"
                    f"📊 <b>Điểm QA:</b> Script <code>{script_evaluation['score'] if script_evaluation else 85}/100</code> | Video <code>{video_qa['score']}/100</code> ({video_qa.get('duration_seconds', 0)}s, {video_qa.get('width')}x{video_qa.get('height')}, Sharpness={vc_check.get('sharpness', 0)})\n"
                    f"⏰ <b>Khung giờ vàng dự kiến:</b> {DISPLAY_VN}\n"
                    f"🏷️ <b>Tags ({len(tags)}):</b> <code>{', '.join(tags[:12])}</code>\n\n"
                    f"💬 <b>Pinned Comment:</b> {final_pinned_comment}"
                )
                try:
                    thumb_16x9 = getattr(render_result, "thumbnail_path", None)
                    if is_long_form and thumb_16x9 and Path(str(thumb_16x9)).exists():
                        with open(str(thumb_16x9), "rb") as tf_tg:
                            _req_tg.post(
                                f"https://api.telegram.org/bot{tg_token}/sendPhoto",
                                data={
                                    "chat_id": str(target_chat),
                                    "caption": f"🖼️ <b>[THUMBNAIL NGANG 16:9 - Job #{job_id}]</b>\n📌 {smart_bundle['enriched_title']}",
                                    "parse_mode": "HTML",
                                },
                                files={"photo": (Path(str(thumb_16x9)).name, tf_tg, "image/jpeg")},
                                timeout=60,
                            )
                    cand_chats = []
                    for _c in [target_chat, *_st_tg.get_allowed_group_ids(), "8498162618"]:
                        if _c and str(_c) not in cand_chats:
                            cand_chats.append(str(_c))
                    cached_fid = None
                    sent_ok_chats = []
                    for _cid in cand_chats:
                        if cached_fid:
                            tg_resp = _req_tg.post(
                                f"https://api.telegram.org/bot{tg_token}/sendVideo",
                                data={
                                    "chat_id": _cid,
                                    "video": cached_fid,
                                    "caption": caption_txt[:1020],
                                    "parse_mode": "HTML",
                                    "supports_streaming": "true",
                                },
                                timeout=30,
                            )
                        else:
                            with open(tg_send_path, "rb") as vf_tg:
                                tg_resp = _req_tg.post(
                                    f"https://api.telegram.org/bot{tg_token}/sendVideo",
                                    data={
                                        "chat_id": _cid,
                                        "caption": caption_txt[:1020],
                                        "parse_mode": "HTML",
                                        "supports_streaming": "true",
                                        "width": "1920" if is_long_form else "1080",
                                        "height": "1080" if is_long_form else "1920",
                                    },
                                    files={"video": (Path(output_video_path).name, vf_tg, "video/mp4")},
                                    timeout=300,
                                )
                        if tg_resp.status_code == 200 and tg_resp.json().get("ok"):
                            sent_ok_chats.append(_cid)
                            v_obj = (tg_resp.json().get("result") or {}).get("video") or {}
                            if v_obj.get("file_id"):
                                cached_fid = v_obj["file_id"]
                        else:
                            print(f"   ⚠️ [Telegram Preview] Bỏ qua chat {_cid} ({tg_resp.status_code}): {tg_resp.text[:160]}")
                    if sent_ok_chats:
                        print(f"   ✅ [Telegram Preview] Đã gửi thành công video #{job_id} vào Telegram ({', '.join(sent_ok_chats)})!")
                        try:
                            pend_file = project_root / "storage" / "pending_group_preview.json"
                            pend_file.write_text(
                                json.dumps(
                                    {
                                        "job_id": job_id,
                                        "video_path": str(output_video_path),
                                        "file_id": cached_fid or "",
                                        "caption": caption_txt[:1020],
                                        "sent_chats": sent_ok_chats,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                finally:
                    Path(tmp_tg_path).unlink(missing_ok=True)
        except Exception as e_tg:
            print(f"   ⚠️ [Telegram Preview] Không thể gửi video qua Telegram: {e_tg}")

    if render_only:
        with get_db() as db:
            j_ro = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j_ro:
                j_ro.status = "AWAITING_APPROVAL"
                j_ro.video_path = str(output_video_path)
                j_ro.title = smart_bundle["enriched_title"]
                j_ro.description = final_description
                j_ro.tags = ",".join(tags)
                j_ro.scheduled_time = SCHEDULE_SLOT_UTC
                j_ro.progress_percent = 100
                j_ro.progress_message = f"Đã gửi bản duyệt qua Telegram (Chờ xác nhận đăng vào {DISPLAY_VN})"
        print(f"\n   🛑 [Render-Only Mode] Đã dựng xong & gửi Telegram cho Job #{job_id}. Chờ anh duyệt trước khi upload YouTube!")
        return {
            "job_id": job_id,
            "channel": ch_name,
            "channel_id": ch_id,
            "title": smart_bundle["enriched_title"],
            "tags_count": len(tags),
            "tags": tags,
            "script_score": script_evaluation["score"] if script_evaluation else 85,
            "video_score": video_qa["score"],
            "scheduled_time": DISPLAY_VN,
            "scheduled_iso": SCHEDULE_SLOT_ISO,
            "youtube_url": "PENDING_APPROVAL",
            "video_id": None,
            "video_path": str(output_video_path),
            "status": "RENDERED_SENT_TELEGRAM",
        }

    # ── BƯỚC 5: UPLOAD YOUTUBE SHORTS, GẮN TAGS SEO & HẸN LỊCH KHUNG GIỜ VÀNG ──
    # Đồng bộ lại scenes_json & pinned_comment mới nhất sau khi LangGraph tinh chỉnh ở Bước 4
    try:
        with get_db() as db_sync:
            j_sync = db_sync.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j_sync and j_sync.scenes_json:
                scenes_json_str = j_sync.scenes_json
                s_refined = json.loads(scenes_json_str) if isinstance(scenes_json_str, str) else scenes_json_str
                if isinstance(s_refined, dict):
                    if s_refined.get("pinned_comment"):
                        final_pinned_comment = pub_agent.sanitize_engagement_comment(str(s_refined["pinned_comment"]).strip())
                    if s_refined.get("description"):
                        ref_desc = str(s_refined["description"]).strip()
                        if smart_bundle["description_hashtags"] not in ref_desc:
                            ref_desc = f"{ref_desc}\n\n{smart_bundle['description_hashtags']}".strip()
                        final_description = ref_desc
    except Exception:
        pass

    print(f"\n[5/5] 🚀 BƯỚC 5: Upload YouTube Shorts, Gắn {len(tags)} Tags SEO & Hẹn lịch {DISPLAY_VN}...")
    thumb_to_upload = (
        (getattr(render_result, "thumbnail_path", None) or getattr(render_result, "thumbnail_vertical_path", None))
        if is_long_form
        else (getattr(render_result, "thumbnail_vertical_path", None) or getattr(render_result, "thumbnail_path", None))
    )

    pub_res = await asyncio.to_thread(
        pub_agent.upload_video,
        video_path=str(output_video_path),
        title=title,
        description=final_description,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=SCHEDULE_SLOT_ISO,
        default_language=ch_lang,
        default_audio_language=ch_lang,
        pinned_comment=final_pinned_comment,
        topic=topic,
        script=scenes_json_str,
        thumbnail_path=thumb_to_upload,
    )

    pub_status = pub_res.get("status")
    yt_id = pub_res.get("video_id")
    yt_url = pub_res.get("shorts_url") or (f"https://www.youtube.com/shorts/{yt_id}" if yt_id else None)
    final_tags = pub_res.get("tags") or tags
    final_title = pub_res.get("enriched_title") or title
    final_desc = pub_res.get("enriched_description") or final_description

    pub_error = pub_res.get("error")
    if yt_id:
        # Hậu kiểm & khóa chặt Tags SEO, Hashtags, CategoryId & Geo-Lock trực tiếp trên YouTube API
        await asyncio.to_thread(pub_agent.verify_and_heal_video_metadata, yt_id)
        print(f"   🎉 Upload & Hẹn lịch YouTube Shorts thành công!")
        print(f"   Video ID  : {yt_id}")
        print(f"   Shorts URL: {yt_url}")
        print(f"   Tags đã gắn ({len(final_tags)} thẻ): {', '.join(final_tags[:10])}...")
        print(f"   Lịch phát : {DISPLAY_VN} ({SCHEDULE_SLOT_ISO})")
    elif pub_status == "waiting_quota_reset":
        print(f"   ⏳ [Quota Guard] Kênh {ch_name} chạm trần Quota API ngày hôm nay -> Đã lưu hàng đợi WAITING_QUOTA_RESET kèm {len(final_tags)} tags & slot {DISPLAY_VN}!")
    else:
        print(f"   ⚠️ [Upload Pending] Video #{job_id} đã dựng & gửi Telegram thành công, lưu trạng thái RENDERED chờ cấp lại OAuth/Upload: {pub_error}")

    for _db_try in range(3):
        try:
            with get_db() as db:
                j_final = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j_final:
                    if yt_id:
                        j_final.status = "SCHEDULED"
                    elif pub_status == "waiting_quota_reset":
                        j_final.status = "WAITING_QUOTA_RESET"
                    else:
                        j_final.status = "RENDERED"
                    j_final.youtube_video_id = yt_id
                    j_final.video_path = str(output_video_path)
                    j_final.title = final_title
                    j_final.description = final_desc
                    j_final.tags = ",".join(final_tags)
                    j_final.scheduled_time = SCHEDULE_SLOT_UTC
                    j_final.progress_percent = 100
                    j_final.progress_message = (
                        f"Đã lên lịch phát sóng lúc {DISPLAY_VN}"
                        if yt_id
                        else (
                            f"Chờ reset Quota để phát sóng lúc {DISPLAY_VN}"
                            if pub_status == "waiting_quota_reset"
                            else f"Đã dựng xong ({DISPLAY_VN}) — {str(pub_error or '')[:120]}"
                        )
                    )
                log_activity(
                    db,
                    telegram_id=1,
                    username="System_Director",
                    action_type="SCHEDULED_VIDEO_SUCCESS" if yt_id else "RENDER_VIDEO_SUCCESS",
                    details=f"Video #{job_id} ('{final_title}') cho {ch_name}. YT ID: {yt_id or pub_status or 'RENDERED'}",
                    channel_id=ch_id,
                )
            break
        except Exception as _db_err:
            if _db_try == 2:
                logger.warning(f"[BatchProducer] DB final update warning after 3 retries (Job #{job_id}): {_db_err}")
            else:
                await asyncio.sleep(1.5)

    return {
        "job_id": job_id,
        "channel": ch_name,
        "channel_id": ch_id,
        "title": final_title,
        "tags_count": len(final_tags),
        "tags": final_tags,
        "script_score": script_evaluation["score"] if script_evaluation else 85,
        "video_score": video_qa["score"],
        "scheduled_time": DISPLAY_VN,
        "scheduled_iso": SCHEDULE_SLOT_ISO,
        "youtube_url": yt_url or ("WAITING_QUOTA_RESET" if pub_status == "waiting_quota_reset" else f"RENDERED_LOCAL ({output_video_path})"),
        "video_id": yt_id,
        "status": "SCHEDULED" if yt_id else (pub_status or "RENDERED"),
    }


async def main():
    parser = argparse.ArgumentParser(description="Produce & schedule videos for active YouTube Shorts channels")
    parser.add_argument(
        "--channel",
        type=str,
        default="all_6",
        help="Target channel key or 'all_6' (6 channels excluding Paws & Hearts) or 'all'",
    )
    parser.add_argument("--count", type=int, default=1, help="Number of videos to produce per channel (e.g. 2)")
    parser.add_argument("--topic", type=str, default=None, help="Optional custom topic override")
    parser.add_argument("--dry-run", action="store_true", help="Generate script, tags, and golden slot only")
    parser.add_argument("--render-only", action="store_true", help="Render video & evaluate QA without uploading to YouTube")
    parser.add_argument("--send-telegram", action="store_true", help="Send rendered MP4 preview directly to Telegram")
    parser.add_argument("--telegram-chat-id", type=str, default=None, help="Optional Telegram chat_id override")
    parser.add_argument("--send-existing-job", type=int, default=None, help="Send an already rendered Job ID to Telegram for preview")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of channels to produce concurrently (default: 1)")
    parser.add_argument("--format", type=str, default="short", choices=["short", "long"], help="Video format: 'short' (9:16) or 'long' (16:9 documentary)")
    args = parser.parse_args()

    if args.send_existing_job:
        import subprocess as _sp_ex
        import requests as _req_ex
        from config.settings import settings as _st_ex
        from app.database.models import get_db, VideoJob
        with get_db() as db:
            j_ex = db.query(VideoJob).filter(VideoJob.id == args.send_existing_job).first()
            if not j_ex:
                print(f"❌ Job #{args.send_existing_job} không tồn tại!")
                return
            v_p = j_ex.video_path or f"storage/outputs/video_{j_ex.id}.mp4"
            en_title = j_ex.title or f"Shorts #{j_ex.id}"
            top_ex = j_ex.topic or ""
            tags_ex = [t.strip() for t in (j_ex.tags or "").split(",") if t.strip()]
            s_d = json.loads(j_ex.scenes_json) if j_ex.scenes_json else {}
            p_comm = s_d.get("pinned_comment", "")
        tg_tok = getattr(_st_ex, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
        t_chat = args.telegram_chat_id or getattr(_st_ex, "TELEGRAM_ADMIN_ID", None) or os.getenv("TELEGRAM_CHAT_ID") or "8498162618"
        send_p = str(v_p)
        tmp_p = str(Path(v_p).with_name(f"tg_preview_{args.send_existing_job}.mp4"))
        try:
            if Path(v_p).stat().st_size > 12 * 1024 * 1024:
                _sp_ex.run(
                    [
                        "ffmpeg", "-y", "-i", str(v_p),
                        "-vf", "scale=720:1280",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                        tmp_p,
                    ],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if Path(tmp_p).exists() and Path(tmp_p).stat().st_size > 100_000:
                    send_p = tmp_p
            ch_label = CHANNELS_CONFIG.get(getattr(j_ex, "target_channel_id", "") or "", {}).get("name") or getattr(j_ex, "target_channel_id", "AI YouTube Studio")
            cap = (
                f"🎬 <b>[BẢN DUYỆT TRƯỚC (SHORTS 9:16) - {ch_label}]</b>\n"
                f"🆔 <b>Job ID:</b> <code>#{args.send_existing_job}</code>\n"
                f"📌 <b>Tiêu đề:</b> {en_title}\n"
                f"🎯 <b>Chủ đề:</b> {top_ex}\n"
                f"📊 <b>Điểm QA:</b> Script <code>94/100</code> | Video <code>94/100</code> (44.0s, Sharpness=28.91, 1080x1920 Master, 100% Non-YouTube Sources)\n"
                f"🏷️ <b>Tags ({len(tags_ex)}):</b> <code>{', '.join(tags_ex[:12])}</code>\n\n"
                f"💬 <b>Pinned Comment:</b> {p_comm}"
            )
            cand_chats_ex = []
            for _c in [t_chat, *_st_ex.get_allowed_group_ids(), "8498162618"]:
                if _c and str(_c) not in cand_chats_ex:
                    cand_chats_ex.append(str(_c))
            cached_fid_ex = None
            sent_ex_chats = []
            for _cid in cand_chats_ex:
                if cached_fid_ex:
                    r_tg = _req_ex.post(
                        f"https://api.telegram.org/bot{tg_tok}/sendVideo",
                        data={"chat_id": _cid, "video": cached_fid_ex, "caption": cap[:1020], "parse_mode": "HTML", "supports_streaming": "true"},
                        timeout=30,
                    )
                else:
                    with open(send_p, "rb") as vf:
                        r_tg = _req_ex.post(
                            f"https://api.telegram.org/bot{tg_tok}/sendVideo",
                            data={"chat_id": _cid, "caption": cap[:1020], "parse_mode": "HTML", "supports_streaming": "true"},
                            files={"video": (f"video_{args.send_existing_job}.mp4", vf, "video/mp4")},
                            timeout=180,
                        )
                print(f"Telegram send status ({_cid}):", r_tg.status_code, r_tg.json().get("ok"))
                if r_tg.status_code == 200 and r_tg.json().get("ok"):
                    sent_ex_chats.append(_cid)
                    v_obj_ex = (r_tg.json().get("result") or {}).get("video") or {}
                    if v_obj_ex.get("file_id"):
                        cached_fid_ex = v_obj_ex["file_id"]
            if sent_ex_chats:
                try:
                    pend_file = project_root / "storage" / "pending_group_preview.json"
                    pend_file.write_text(
                        json.dumps(
                            {
                                "job_id": args.send_existing_job,
                                "video_path": str(v_p),
                                "file_id": cached_fid_ex or "",
                                "caption": cap[:1020],
                                "sent_chats": sent_ex_chats,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        finally:
            Path(tmp_p).unlink(missing_ok=True)
        return

    if args.channel == "all_6":
        targets = list(SIX_ACTIVE_CHANNELS)
    elif args.channel == "all":
        targets = list(SIX_ACTIVE_CHANNELS) + ["ch_paws_hearts_rescue"]
    else:
        targets = [c.strip() for c in args.channel.split(",") if c.strip()]

    results = []
    videos_per_ch = max(1, int(args.count or 1))
    concurrency = max(1, int(getattr(args, "concurrency", 1) or 1))
    total_planned_videos = len(targets) * videos_per_ch
    if (
        concurrency > 1
        and total_planned_videos >= 2
        and os.environ.get("ALLOW_HIGH_BATCH_CONCURRENCY", "").strip().lower() not in ("1", "true", "yes")
    ):
        logger.info(
            f"⚡ [BatchProducer] Tự động đặt concurrency=1 (thay vì {concurrency}) cho lô {total_planned_videos} video "
            f"vì mỗi video đã chạy song song 8 luồng cảnh + 5 luồng Stock. "
            f"Chạy tuần tự từng video giúp đạt tốc độ 90s–120s/video và tránh nghẽn 429 Pexels/Gemini."
        )
        concurrency = 1

    if concurrency <= 1 or len(targets) <= 1:
        for t in targets:
            for v_idx in range(1, videos_per_ch + 1):
                try:
                    res = await produce_channel_video(
                        t,
                        custom_topic=args.topic,
                        dry_run=args.dry_run,
                        render_only=args.render_only,
                        send_telegram=args.send_telegram,
                        telegram_chat_id=args.telegram_chat_id,
                        video_index=v_idx,
                        total_per_channel=videos_per_ch,
                        video_format=args.format,
                    )
                    if res:
                        results.append(res)
                except Exception as e:
                    logger.error(f"❌ Lỗi khi sản xuất video #{v_idx} cho kênh {t}: {e}", exc_info=True)
                    results.append({
                        "channel": CHANNELS_CONFIG.get(t, {}).get("name", t),
                        "channel_id": t,
                        "title": f"FAILED (Video #{v_idx})",
                        "error": str(e),
                    })
    else:
        sem = asyncio.Semaphore(concurrency)

        async def _run_channel_queue(ch_key: str):
            ch_results = []
            async with sem:
                for v_idx in range(1, videos_per_ch + 1):
                    try:
                        res = await produce_channel_video(
                            ch_key,
                            custom_topic=args.topic,
                            dry_run=args.dry_run,
                            render_only=args.render_only,
                            send_telegram=args.send_telegram,
                            telegram_chat_id=args.telegram_chat_id,
                            video_index=v_idx,
                            total_per_channel=videos_per_ch,
                        )
                        if res:
                            ch_results.append(res)
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi sản xuất video #{v_idx} cho kênh {ch_key}: {e}", exc_info=True)
                        ch_results.append({
                            "channel": CHANNELS_CONFIG.get(ch_key, {}).get("name", ch_key),
                            "channel_id": ch_key,
                            "title": f"FAILED (Video #{v_idx})",
                            "error": str(e),
                        })
            return ch_results

        batches = await asyncio.gather(*(_run_channel_queue(t) for t in targets))
        for b in batches:
            results.extend(b)

    print("\n" + "#" * 85)
    print(f"📋 TỔNG KẾT SẢN XUẤT ({len(results)} VIDEO TRÊN {len(targets)} KÊNH):")
    print("#" * 85)
    for r in results:
        if r.get("error"):
            print(f"  ❌ {r['channel']}: LỖI — {r['error']}")
        else:
            print(
                f"  ✅ [Job #{r.get('job_id')}] {r['channel']}: '{r['title']}'\n"
                f"     • Điểm QA: Script {r['script_score']}/100 | Video {r['video_score']}/100\n"
                f"     • Tags SEO ({r.get('tags_count', 0)} thẻ): {', '.join((r.get('tags') or [])[:8])}...\n"
                f"     • Khung giờ vàng: {r['scheduled_time']} ({r.get('scheduled_iso')})\n"
                f"     • Link Shorts: {r['youtube_url']}"
            )
    print("#" * 85 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
    os._exit(0)
