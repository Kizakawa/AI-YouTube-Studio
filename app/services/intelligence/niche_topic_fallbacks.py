"""app.services.intelligence.niche_topic_fallbacks — Kho chủ đề dự phòng chuẩn triệu view theo từng ngách & ngôn ngữ."""
import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

import requests as http_requests

from config.settings import settings
from app.database.models import get_db, VideoJob, User, ActivityLog, log_activity
from app.schemas.contracts import JobStatus, ScriptContract
from app.services.youtube_analytics import fetch_channel_live_stats
from app.services.niche_manager import get_niche, build_script_prompt_instructions
from app.services.smart_scheduler import get_next_available_golden_slot
from app.agents.content_agent import ContentAgent

logger = logging.getLogger(__name__)

# Giới hạn tối đa không có video mới trước khi kích hoạt Auto-Generation khẩn cấp (48 tiếng = 2 ngày)
KPI_CRITICAL_OVERDUE_HOURS = 48.0
# Ngưỡng cảnh báo màu vàng (24 tiếng = 1 ngày)
KPI_WARNING_HOURS = 24.0
# Thời gian chờ duyệt trước khi tự động đăng tạm (mặc định 4 tiếng)
DEFAULT_APPROVAL_GRACE_HOURS = 4.0



def _get_curated_niche_fallbacks(niche_id: str, lang: str = "en") -> List[Dict[str, Any]]:
    """Kho ý tưởng dự phòng chất lượng cao cho từng ngách."""
    if "history" in niche_id or "what_if" in niche_id:
        return [
            {
                "topic": "Vasili Arkhipov and the 1962 Soviet Submarine B-59 Nuclear Torpedo Veto",
                "title": "One Man Refused The Order To End The World ☢️🚢 #Shorts",
                "hook": "On October 27, 1962, two Soviet captains voted to fire a nuclear torpedo at the US Navy.",
                "angle": "Submarine B-59 depth charges, 3-officer launch protocol, Vasili Arkhipov's lone veto",
                "pinned_comment": "If Vasili Arkhipov hadn't been on board B-59 that day, would civilization still exist? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "165% - 175%",
                "why_viral": "Khoảnh khắc lịch sử một con người ngăn chặn Thế chiến 3 chỉ bằng 1 lá phiếu phủ quyết",
                "fixes_flaw": "Tập trung vào khoảnh khắc căng thẳng nghẹt thở trong tàu ngầm B-59 năm 1962.",
                "trend_alignment": "Lịch sử bước ngoặt & What-If (Historical Single-Second Turning Points)."
            },
            {
                "topic": "Stanislav Petrov and the 1983 Soviet Nuclear Early-Warning False Alarm",
                "title": "28 Minutes From WW3: The Man Who Disobeyed Protocol 🚨🌍 #Shorts",
                "hook": "In 1983, Soviet satellites flashed five incoming American nuclear missiles in red.",
                "angle": "Oko satellite sunlight glitch, protocol demanded immediate retaliation, Petrov's gut instinct",
                "pinned_comment": "Would you have trusted your gut over a military computer screen? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Quyết định sinh tử của Stanislav Petrov trái lệnh máy tính cứu cả nhân loại năm 1983",
                "fixes_flaw": "Đánh thẳng vào tâm lý nghẹt thở của 1 quyết định lịch sử có thật 100%.",
                "trend_alignment": "Lịch sử chiến tranh lạnh & quyết định định mệnh."
            },
            {
                "topic": "What If The 3 Chernobyl Suicide Divers Failed To Drain The Reactor Pool In 1986?",
                "title": "3 Men Dove Into Radioactive Water To Save Europe ☢️🤿 #Shorts",
                "hook": "In 1986, Chernobyl's melting core was 48 hours away from hitting a giant water tank and wiping out half of Europe.",
                "angle": "Chernobyl thermal explosion risk, Alexei Ananenko and Valeri Bezpalov diving in pitch black radioactive water",
                "pinned_comment": "Why don't school history books teach the names of these 3 men? Debate below 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "165% - 175%",
                "why_viral": "Khoảnh khắc sinh tử có thật 100% với mức độ nguy hiểm huỷ diệt lục địa",
                "fixes_flaw": "Thay thế tiêu đề mơ hồ ('What If This Chauffeur...') bằng tình huống sinh tử hạt nhân rõ ràng ngay giây đầu.",
                "trend_alignment": "Lịch sử bước ngoặt & What-If (Historical Single-Second Turning Points)."
            }
        ]
    elif "mystery" in niche_id or "deep_sea" in niche_id:
        if str(lang or "").lower().startswith("pt"):
            return [
                {
                    "topic": "Colossal Squid vs Sperm Whale: Battle Scars From 2,000 Meters Deep In The Southern Ocean",
                    "title": "Lula-Colossal vs Cachalote: O Duelo Mortal A 2.000m No Oceano 🦑🐋 #Shorts",
                    "hook": "A 2.000 metros de profundidade no oceano escuro, cachalotes emergem cobertos por cicatrizes de ventosas com garras giratórias.",
                    "angle": "Colossal squid rotating hooks vs sperm whale sonar click stun at 2,000m ocean depth",
                    "pinned_comment": "Quem vence na escuridão total do oceano profundo: Cachalote ou Lula-Colossal? Comente! 👇",
                    "viral_formula": "Debate in Comments",
                    "predicted_retention": "165% - 175%",
                    "why_viral": "Cuộc chiến sinh tử dưới đáy đại dương giữa Cá nhà táng và Mực khổng lồ chuẩn ngách biển sâu Brazil",
                    "fixes_flaw": "100% đại dương sâu cho kênh Mistérios do Mundo, tách biệt hoàn toàn với kênh trên cạn.",
                    "trend_alignment": "Thủy quái biển sâu & đối đầu đại dương."
                },
                {
                    "topic": "Point Nemo: The Spacecraft Cemetery In The Middle Of The Pacific Ocean",
                    "title": "Ponto Nemo: O Cemitério Secreto De Naves No Fundo Do Oceano 🌊🛰️ #Shorts",
                    "hook": "Existe um ponto no Oceano Pacífico tão isolado que os humanos mais próximos estão na Estação Espacial Internacional.",
                    "angle": "Point Nemo oceanic pole of inaccessibility, 2,688 km from land, sunken space stations at 4,000m depth",
                    "pinned_comment": "Você teria coragem de navegar pelo Ponto Nemo à noite? 👇",
                    "viral_formula": "Curiosity Part 2",
                    "predicted_retention": "160% - 170%",
                    "why_viral": "Bí ẩn vùng biển cô độc nhất Thái Bình Dương nơi chôn cất tàu vũ trụ dưới đáy đại dương",
                    "fixes_flaw": "100% chủ đề biển cả & đại dương sâu chuẩn pt-BR.",
                    "trend_alignment": "Khám phá tọa độ tử thần trên đại dương."
                },
                {
                    "topic": "The 400-Year-Old Greenland Shark: The Blind Apex Predator Of The Freezing Arctic Ocean",
                    "title": "O Tubarão De 400 Anos Que Vive Na Escuridão Do Oceano Ártico 🦈❄️ #Shorts",
                    "hook": "Nas águas congelantes do Oceano Ártico, nada um tubarão gigante que nasceu antes mesmo do Império Brasileiro existir.",
                    "angle": "Greenland shark 400-year lifespan, antifreeze TMAO blood at 2,200m ocean depth",
                    "pinned_comment": "Qual segredo mais assustador você acha que o oceano ainda esconde? 👇",
                    "viral_formula": "Debate in Comments",
                    "predicted_retention": "160% - 170%",
                    "why_viral": "Sinh vật đại dương sống 400 năm dưới đáy Bắc Băng Dương kích thích tò mò cực lớn",
                    "fixes_flaw": "100% sinh vật biển sâu cho kênh Mistérios do Mundo.",
                    "trend_alignment": "Sinh vật bất tử dưới đáy đại dương."
                }
            ]
        return [
            {
                "topic": "Colossal Squid vs Sperm Whale: Battle Scars From 6,500 Feet Deep In The Ocean",
                "title": "Colossal Squid vs Sperm Whale At 6,500 Feet Deep 🦑🐋 #Shorts",
                "hook": "At 6,500 feet below the ocean surface, 50-ton sperm whales surface covered in jagged circular scars from a ruthless abyssal hunter.",
                "angle": "Colossal squid swiveling tentacle hooks vs sperm whale 230-decibel sonar click in pitch-black ocean",
                "pinned_comment": "In pitch-black water at 6,500 feet, who rules the deep ocean? Drop your vote 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Cuộc đối đầu dưới đáy biển sâu giữa Cá nhà táng và Mực khổng lồ",
                "fixes_flaw": "100% chủ đề đại dương và biển sâu.",
                "trend_alignment": "Bí ẩn đại dương và quái vật biển sâu."
            },
            {
                "topic": "Point Nemo: Why Spaceships Are Crashed Into The Most Isolated Spot In The Pacific Ocean",
                "title": "The Secret Spacecraft Graveyard At The Bottom Of The Ocean 🌊🛰️ #Shorts",
                "hook": "In the middle of the South Pacific Ocean lies a graveyard 1,670 miles from land where 260 spacecraft rest on the seafloor.",
                "angle": "Point Nemo oceanic pole of inaccessibility, 13,000-foot ocean depth, astronauts overhead closer than land",
                "pinned_comment": "Would you dare sail across Point Nemo at night? 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "155% - 165%",
                "why_viral": "Nghĩa địa tàu vũ trụ dưới đáy Thái Bình Dương",
                "fixes_flaw": "100% chủ đề đại dương sâu.",
                "trend_alignment": "Khám phá vùng biển bí ẩn nhất hành tinh."
            },
            {
                "topic": "The 1997 Upsweep Sound: The Unexplained Pacific Ocean Signal Recorded By NOAA Hydrophones",
                "title": "The 1997 Deep Ocean Sound Scientists Still Can't Explain 🌊🔊 #Shorts",
                "hook": "In August 1997, underwater hydrophones across the Pacific Ocean recorded a repeating train of sounds rising from the abyss.",
                "angle": "NOAA Pacific marine acoustic monitoring, 4,000-mile hydrophone detection range, volcanic abyssal vents",
                "pinned_comment": "What do you think produced the Upsweep sound on the Pacific seafloor? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "155% - 165%",
                "why_viral": "Âm thanh bí ẩn dưới đáy Thái Bình Dương được NOAA ghi lại",
                "fixes_flaw": "100% chủ đề đại dương sâu.",
                "trend_alignment": "Tín hiệu âm thanh bí ẩn dưới đáy đại dương."
            }
        ]
    elif "animal" in niche_id or "rescue" in niche_id or "pet" in niche_id:
        return [
            {
                "topic": "The Stray Puppy Who Grabbed A Rescuer's Coat And Led Them To A Storm Drain",
                "title": "He Refused To Let Go Until They Followed Him 🥺🐾 #Shorts",
                "hook": "This stray puppy bit onto a stranger's coat and refused to let go for a heartbreaking reason...",
                "angle": "Urgent plea for help, leading rescuer to trapped littermates, full joyful adoption recovery",
                "pinned_comment": "Drop a ❤️ if you believe every stray animal deserves a miracle! 👇",
                "viral_formula": "Emotional Rollercoaster & Tears to Joy",
                "predicted_retention": "170% - 185%",
                "why_viral": "Cấu trúc mồi câu tò mò chủ động kết hợp hành trình giải cứu 4 giai đoạn",
                "fixes_flaw": "Thay thế các tiêu đề bi lụy đơn thuần bằng mồi câu tò mò chủ động ('He Refused To Let Go Until...'), kéo dài thời lượng 50-55s có Payoff hạnh phúc.",
                "trend_alignment": "Cấu trúc biến đổi 4 giai đoạn (4-Stage Emotional Transformation Arc) đạt tỷ lệ chuyển đổi Subscribe cao nhất."
            },
            {
                "topic": "Terrified Shelter Dog Hid Facing The Wall Until One Rescuer Sat Quietly Beside Him",
                "title": "The Exact Moment This Broken Dog Dared To Trust Again 🥺❤️ #Shorts",
                "hook": "For three weeks at the shelter, he stared at the concrete wall and trembled whenever anyone walked by...",
                "angle": "Overcoming severe trauma, patient rescuer sitting on floor, first gentle paw touch, joyful tail wag",
                "pinned_comment": "Have you ever rescued an animal? Tell us their name below! 👇",
                "viral_formula": "Double Tap & Healing Empathy",
                "predicted_retention": "165% - 175%",
                "why_viral": "Khoảnh khắc chuyển biến cảm xúc chân thực — chữa lành tâm hồn",
                "fixes_flaw": "Tập trung vào hành động thực tế của chú chó từ sợ hãi sang chủ động đặt chân lên tay người cứu hộ.",
                "trend_alignment": "Nội dung chữa lành tâm hồn (Healing & Empathy) có tỷ lệ lưu lại (Save) cao nhất trên nền tảng."
            },
            {
                "topic": "Stray Dog Risks Its Own Life to Drag An Injured Kitten Out Of Highway Traffic",
                "title": "Stray Dog RISKS Life to Save A Tiny Kitten 🐕🐱 #Shorts",
                "hook": "Cars were speeding past at 50 mph, but this stray dog saw something moving in the road...",
                "angle": "Cross-species empathy, heroic rescue instinct, inseparable animal brotherhood",
                "pinned_comment": "Animals truly have purer hearts than humans. Agree or disagree? 👇",
                "viral_formula": "Debate in Comments & Heroic Miracle",
                "predicted_retention": "160% - 170%",
                "why_viral": "Câu chuyện động vật cứu nhau luôn đạt triệu view nhờ tính kỳ tích phi thường",
                "fixes_flaw": "Tạo tình huống khẩn cấp giật gân ngay giây đầu tiên, chặn đứng tỷ lệ lướt qua (Swipe Away Rate).",
                "trend_alignment": "Kỳ tích động vật cứu nhau xuyên loài (Cross-species rescue miracle) luôn chạm mốc triệu view thế giới."
            }
        ]
    elif "wildlife" in niche_id or "predator" in niche_id or "nature" in niche_id:
        if str(lang or "").lower().startswith("pt"):
            return [
                {
                    "topic": "Jaguar vs Black Caiman: How Fangs Punch Straight Through Armored Reptile Skulls Like Tin Cans",
                    "title": "Por Que O Jacaré-Açu Tem Pavor Da Onça-Pintada No Pantanal 🐆🐊 #Shorts",
                    "hook": "Enquanto leões miram na garganta, a onça-pintada crava as presas direto no crânio blindado do jacaré como se furasse uma lata vazia.",
                    "angle": "Jaguar skull-piercing fangs punching through armored reptile bone vs Black Caiman in Pantanal rivers",
                    "pinned_comment": "Quem domina o Pantanal: Onça-Pintada ou Jacaré-Açu? Deixe seu voto nos comentários! 👇",
                    "viral_formula": "Shock Fact & Apex Predator",
                    "predicted_retention": "175% - 185%",
                    "why_viral": "Cuộc đối đầu kinh điển trên cạn/đầm lầy Pantanal giữa Báo đốm và Cá sấu Jacaré-açu",
                    "fixes_flaw": "100% động vật săn mồi trên cạn Brazil cho Odisseia Selvagem.",
                    "trend_alignment": "Giải phẫu vũ khí săn mồi trên cạn."
                },
                {
                    "topic": "Giant River Otter Pack vs Jaguar: How The 'River Wolves' Defend Their Territory",
                    "title": "Por Que Até A Onça-Pintada Recua Diante De Um Bando De Ariranhas 🦦🐆 #Shorts",
                    "hook": "Sozinha, a onça-pintada reina na selva — mas quando oito ariranhas cercam a margem, até o maior felino das Américas recua.",
                    "angle": "Giant river otter pack coordination, razor teeth and territorial defense against a lone jaguar",
                    "pinned_comment": "Você sabia que as ariranhas são chamadas de 'lobos do rio' na Amazônia? 👇",
                    "viral_formula": "Adrenaline Surge & Predator Biomechanics",
                    "predicted_retention": "170% - 180%",
                    "why_viral": "Cuộc đối đầu bất ngờ giữa bầy Rái cá khổng lồ Amazon và Báo đốm",
                    "fixes_flaw": "100% động vật hoang dã trên cạn/rừng Amazon cho Odisseia Selvagem.",
                    "trend_alignment": "Chiến thuật săn mồi bầy đàn trên cạn."
                },
                {
                    "topic": "Honey Badger vs King Cobra: How It Sleeps Off Lethal Neurotoxic Venom",
                    "title": "Texugo-do-Mel vs Cobra-Rei: O Predador Imune Ao Veneno Mortal 🦡🐍 #Shorts",
                    "hook": "Uma única picada da cobra-rei derruba um elefante em três horas — mas o texugo-do-mel apenas tira uma soneca e volta para terminar a refeição.",
                    "angle": "Honey badger nicotinic receptor mutation blocking alpha-neurotoxin, thick loose skin defense",
                    "pinned_comment": "Existe algum animal terrestre mais destemido que o Texugo-do-Mel? 👇",
                    "viral_formula": "Shock Fact & Apex Predator",
                    "predicted_retention": "165% - 175%",
                    "why_viral": "Khả năng miễn nhiễm nọc độc rắn hổ mang chúa của Lửng mật trên cạn",
                    "fixes_flaw": "100% động vật săn mồi trên cạn.",
                    "trend_alignment": "Đột biến sinh học kỳ diệu của thú săn mồi trên cạn."
                }
            ]
        return [
            {
                "topic": "Harpy Eagle vs Tree Sloth: Why Talons Larger Than Grizzly Bear Claws Snatch Prey Mid-Flight",
                "title": "The Giant Eagle With Claws Bigger Than A Grizzly Bear! 🦅🔥 #Shorts",
                "hook": "With talons larger than a grizzly bear's claws, the Harpy Eagle snatches 20-pound prey straight off tree branches at 50 mph.",
                "angle": "Bone-crushing grip with claws bigger than a grizzly bear's, canopy radar facial disc, snatching sloths mid-flight",
                "pinned_comment": "Is the Harpy Eagle the most terrifying bird on Earth? Drop your vote below! 👇",
                "viral_formula": "Shock Fact & Apex Predator",
                "predicted_retention": "175% - 185%",
                "why_viral": "Cấu trúc đối đầu trực diện A vs B kèm ẩn dụ móng vuốt to hơn móng gấu xám cực dễ hình dung",
                "fixes_flaw": "Nhân bản chủ đề đại bàng Harpia đã thắng lớn sang bản tiếng Anh.",
                "trend_alignment": "Giải phẫu vũ khí săn mồi hạng nặng của tự nhiên."
            },
            {
                "topic": "Komodo Dragon vs Water Buffalo: How One Venomous Bite Takes Down A 1,200lb Giant",
                "title": "How One Bite From A Komodo Dragon Drops A 1,200lb Giant 🦎💀 #Shorts",
                "hook": "A Water Buffalo outweighs a Komodo Dragon by 1,000 pounds... yet one single bite seals its fate.",
                "angle": "Serrated iron-coated teeth, anticoagulant venom glands, relentless patient tracking",
                "pinned_comment": "Could any land predator survive a Komodo Dragon bite? 👇",
                "viral_formula": "Adrenaline Surge & Predator Biomechanics",
                "predicted_retention": "165% - 175%",
                "why_viral": "Cuộc đối đầu bất đối xứng giữa Rồng Komodo và Trâu rừng luôn thu hút triệu view",
                "fixes_flaw": "Mở đầu bằng cảnh đớp mồi thực tế tốc độ cao trên YouTube Shorts.",
                "trend_alignment": "Giải phẫu cơ sinh học động vật săn mồi (Predator Biomechanics)."
            }
        ]
    else:  # psychology / default
        return [
            {
                "topic": "Why FBI Interrogators Watch Your Feet Instead Of Your Eyes When You Lie",
                "title": "Why FBI Agents Watch Your FEET When You Lie 🕵️‍♂️🧠 #Shorts",
                "hook": "A trained liar can control their face and voice for hours, but they always forget to control their feet.",
                "angle": "Limbic system freeze response, foot-pointing toward the exit door, Joe Navarro FBI behavioral profiling",
                "pinned_comment": "Have you ever caught someone lying by watching their body language? 👇",
                "viral_formula": "Double Tap & Behavioral Secret",
                "predicted_retention": "165% - 175%",
                "why_viral": "Mẹo đọc vị ngôn ngữ cơ thể của FBI luôn đạt tỷ lệ dừng xem (Stay%) trên 65%",
                "fixes_flaw": "Thoát khỏi chuỗi video 'Silence Trick / 3 Signs Toxic' bị lặp lại, chuyển sang kỹ thuật thẩm vấn FBI cực kỳ trực quan.",
                "trend_alignment": "Tâm lý học hành vi & Ngôn ngữ cơ thể FBI (Behavioral Profiling)."
            },
            {
                "topic": "Why Guilty Suspects Always Repeat Your Question Before Answering In Police Rooms",
                "title": "The 1 Verbal Glitch That Exposes A Liar Instantly 🗣️🚨 #Shorts",
                "hook": "When you ask someone a direct question and they repeat your exact words back to you, their brain is stalling for time.",
                "angle": "Cognitive load in deception, echo-questioning to buy 2 seconds to fabricate an alibi, police interrogation transcript analysis",
                "pinned_comment": "Try this test the next time someone hides the truth from you! 👇",
                "viral_formula": "Debate in Comments & Practical Test",
                "predicted_retention": "160% - 170%",
                "why_viral": "Dấu hiệu ngôn từ cụ thể dễ kiểm chứng ngay trong đời thực",
                "fixes_flaw": "Cung cấp dấu hiệu phát hiện nói dối ngay lập tức trong 3 giây đầu kèm cảnh thẩm vấn thực tế.",
                "trend_alignment": "Giải mã tâm lý tội phạm và phát hiện nói dối (Deception Detection)."
            },
            {
                "topic": "The 1971 Stanford Prison Experiment: How Ordinary Students Turned Ruthless In 6 Days",
                "title": "The Psychology Experiment Cancelled After Just 6 Days 🚨🧠 #Shorts",
                "hook": "In 1971, psychologists locked 24 healthy college students in a fake basement prison — by day six, they had to shut it down in horror.",
                "angle": "Philip Zimbardo's Stanford Prison Experiment, instant power corruption, deindividuation behind mirrored sunglasses",
                "pinned_comment": "Does power corrupt good people, or does it just reveal who they really are? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 175%",
                "why_viral": "Các thí nghiệm tâm lý học bị cấm trong lịch sử luôn có tỷ lệ xem hết video (APV) cao nhất ngách tâm lý",
                "fixes_flaw": "Kể chuyện thực nghiệm có thật với tư liệu hình ảnh lịch sử thay vì lời khuyên chung chung.",
                "trend_alignment": "Thí nghiệm tâm lý học đen tối có thật trong lịch sử (Forbidden Dark Psychology Experiments)."
            }
        ]
