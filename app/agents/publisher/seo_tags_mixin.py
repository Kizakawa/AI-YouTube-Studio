"""app.agents.publisher.seo_tags_mixin — Sinh bộ thẻ Tags/Hashtags SEO chuẩn thuật toán & làm sạch mô tả Shorts."""
import os
import sys
import re
import json
import time
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from config.settings import settings
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)


class SeoTagsMixin:
    def resolve_locked_category_id(self) -> str:
        """
        Khóa cứng Category ID chuẩn cho từng kênh (Tuyệt đối chặn cat=27 Education gây bóp hiển thị 0-4 view trên Shorts Feed).
        - Kênh Động vật hoang dã săn mồi (wildlife_predators: Wild Odyssey, Odisseia Selvagem) -> '15' (Pets & Animals)
        - Toàn bộ các kênh còn lại (Mistérios do Mundo, Mysteries EN, Psychology, Alternate History, Paws & Hearts) -> '22' (People & Blogs)
        """
        FORBIDDEN_CATEGORIES = {"27"}  # Tuyệt đối cấm Education (27) cho kênh Shorts giải trí/tò mò
        explicit_cat = str(self.channel_config.get("youtube_category_id") or "").strip()
        raw_niche = str(self.channel_config.get("niche") or "").lower()

        if explicit_cat in FORBIDDEN_CATEGORIES:
            logger.warning(
                f"🛡️ [CategoryFirewall] Chặn categoryId='{explicit_cat}' (bị cấm trên Shorts) cho kênh '{self.channel_name}' -> Ép về '22' (People & Blogs)!"
            )
            return "22"

        if explicit_cat in ("15", "22", "24"):
            if explicit_cat == "15" and not any(k in raw_niche for k in ("wildlife", "predator", "rescue", "animal", "pet")):
                return "22"
            return explicit_cat

        if any(k in raw_niche for k in ("wildlife", "predator", "rescue", "animal", "pet")):
            return "15"
        return "22"

    def build_smart_video_tags_and_hashtags(
        self,
        title: str,
        topic: str = "",
        script: str = "",
        extra_tags: Optional[Any] = None,
        is_long_form: bool = False,
    ) -> Dict[str, Any]:
        """
        Tự động tổng hợp bộ Tags SEO (`snippet.tags`) và Hashtags chuẩn ngách & sát chủ đề thực tế của video:
        - Nếu `is_long_form=False` (Shorts 9:16): Gắn `#Shorts` + 2-3 hashtag chuẩn ngách trên Tiêu đề.
        - Nếu `is_long_form=True` (Video Dài 16:9): KHÔNG gắn `#Shorts` lên Tiêu đề/Mô tả/Tags để YouTube phân phối đúng luồng Long-Form Browse/Suggested.
        """
        if not is_long_form and script:
            if '"video_format": "long"' in str(script) or "'video_format': 'long'" in str(script):
                is_long_form = True
        raw_niche = str(self.channel_config.get("niche") or "").lower()
        cfg_lang = str(self.channel_config.get("language") or "").strip().lower()
        is_br = cfg_lang.startswith("pt")

        # Làm sạch tiêu đề gốc (bỏ mọi chuỗi #hashtag ở cuối)
        base_title = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', (title or "").strip()).strip()
        base_title = re.sub(r'#shorts\b', '', base_title, flags=re.IGNORECASE).strip()
        if not base_title:
            base_title = (topic or ("Documentary Video" if is_long_form else "Shorts Video")).strip()

        # Danh sách từ khóa cấm / sai chính tả / từ đơn lẻ vô nghĩa bị tách từ tiêu đề
        BLACKLIST_TAGS = {
            "narcisist", "masteringdiscipline", "legacyofalexander", "vn", "vietnam",
            "vietnamese", "tiengviet", "variant", "option", "test", "undefined", "null",
            "como", "única", "unica", "queda", "metros", "segredo", "cima", "nunca",
            "olham", "jogadas", "ponto", "flees", "foot", "great", "white", "bottom",
            "live", "secret", "drops", "down", "shut", "word", "being", "controlled",
            "signs", "esmaga", "acabou", "imitar", "pulled", "sleeve", "exact", "second",
            "tiny", "looked", "refused", "until", "tied", "shivering", "found", "saving",
            "alone", "happened", "thought", "order", "rewrote", "sank", "does", "endless",
            "cooks", "degree", "captured", "unthinkable", "flee", "kill", "kicks",
            "crushes", "deadliest", "clue", "hidden", "stop", "trick", "watch", "body",
            "language", "sperm", "whale", "squid", "colossal", "harpy", "eagle", "howler",
            "king", "cobra", "polar", "bears", "diante", "treme", "branco", "enfrentar",
            "odeiam", "choque", "ferve", "perigosa", "erro",
        }
        text_context = f"{base_title} {topic or ''}".lower()

        # Cấu hình bộ Hashtag Tiêu đề & Tags SEO cốt lõi theo từng Ngách + Ngôn ngữ
        if any(k in raw_niche for k in ("wildlife", "predator")):
            if is_br:
                niche_title_hashes = ["vidaanimal", "animais", "natureza"]
                niche_core_tags = [
                    "vida selvagem", "animais", "natureza", "mundo animal", "predadores",
                    "reino animal", "curiosidades animais", "batalha animal", "documentario animal",
                    "selva", "brasil", "Shorts", "shorts", "ytshorts", "viral",
                ]
            else:
                niche_title_hashes = ["wildlife", "animals", "nature"]
                niche_core_tags = [
                    "wildlife", "animals", "nature", "animal kingdom", "apex predator",
                    "wild animals", "nature documentary", "animal facts", "wildlife battle",
                    "predator", "usa", "Shorts", "shorts", "ytshorts", "viral",
                ]
        elif "psychology" in raw_niche:
            niche_title_hashes = ["psychology", "humanbehavior", "mindset"]
            niche_core_tags = [
                "psychology", "human behavior", "body language", "dark psychology",
                "psychology facts", "mindset", "social skills", "subconscious mind",
                "behavioral psychology", "usa", "Shorts", "shorts", "ytshorts", "viral",
            ]
        elif any(k in raw_niche for k in ("deep_sea", "ocean", "mystery", "earth")):
            if is_br:
                niche_title_hashes = ["misterios", "curiosidades", "ciencia"]
                niche_core_tags = [
                    "misterios", "curiosidades", "ciencia", "misterios do mundo", "oceano",
                    "descobertas", "fatos desconhecidos", "fenomenos naturais", "fundo do mar",
                    "brasil", "Shorts", "shorts", "ytshorts", "viral",
                ]
            else:
                niche_title_hashes = ["mystery", "science", "ocean"]
                niche_core_tags = [
                    "mystery", "science facts", "unexplained mysteries", "deep sea",
                    "ocean mysteries", "earth mysteries", "scientific discovery", "nature mystery",
                    "facts", "usa", "Shorts", "shorts", "ytshorts", "viral",
                ]
        elif any(k in raw_niche for k in ("history", "what_if", "alternate")):
            niche_title_hashes = ["history", "whatif", "documentary"]
            niche_core_tags = [
                "history", "alternate history", "what if", "what if history",
                "historical facts", "crazy history", "historical disasters",
                "world history", "american history", "history documentary",
                "biggest mistakes in history", "butterfly effect", "cold war",
                "dark history", "usa", "Shorts", "shorts", "ytshorts", "viral",
            ]
        elif any(k in raw_niche for k in ("rescue", "pet", "animal_rescue")):
            niche_title_hashes = ["animalrescue", "dogrescue", "wholesome"]
            niche_core_tags = [
                "animal rescue", "dog rescue", "cat rescue", "wholesome", "saved animals",
                "heartwarming", "pet rescue", "stray dog", "faith in humanity",
                "usa", "Shorts", "shorts", "ytshorts", "viral",
            ]
        else:
            niche_title_hashes = ["facts", "mystery", "viral"]
            niche_core_tags = ["facts", "mystery", "documentary", "Shorts", "shorts", "ytshorts", "viral"]

        # Chặn các tag chéo ngách nếu nội dung thực tế của video không hề nhắc tới
        CROSS_NICHE_GUARDS = {
            "space": ("space", "planet", "galaxy", "nasa", "black hole", "astronaut", "universe", "espaco", "planeta", "spacecraft", "espaçonaves"),
            "titanic": ("titanic", "shipwreck", "iceberg", "1912"),
            "alexander": ("alexander", "macedon"),
            "narcissist": ("narcissist", "narcissism", "toxic"),
            "motivation": ("motivation", "discipline", "habit"),
            "bigcat": ("lion", "lions", "tiger", "jaguar", "leopard", "cheetah", "cougar", "puma", "panther", "big cat", "onça", "leão", "tigre", "leopardo"),
            "big cat": ("lion", "lions", "tiger", "jaguar", "leopard", "cheetah", "cougar", "puma", "panther", "big cat", "onça", "leão", "tigre", "leopardo"),
            "amazonia": ("amazon", "amazônia", "amazonia", "pantanal", "selva", "onça", "sucuri", "jacaré", "harpia", "ariranha", "poraquê", "preguiça"),
            "amazônia": ("amazon", "amazônia", "amazonia", "pantanal", "selva", "onça", "sucuri", "jacaré", "harpia", "ariranha", "poraquê", "preguiça"),
        }

        def _is_tag_allowed(raw_t: str) -> bool:
            t_clean = re.sub(r'^#+', '', (raw_t or "").strip()).lower()
            if not t_clean or len(t_clean) < 2 or len(t_clean) > 40:
                return False
            if t_clean in BLACKLIST_TAGS:
                return False
            for guard_key, required_words in CROSS_NICHE_GUARDS.items():
                if guard_key in t_clean and not any(w in text_context for w in required_words):
                    return False
            return True

        # 1. Thu thập extra_tags do người dùng/script truyền vào + default_tags/description_tags của kênh
        custom_tags: List[str] = []
        for src in (extra_tags, self.channel_config.get("default_tags"), self.channel_config.get("description_tags")):
            if isinstance(src, list):
                custom_tags.extend(str(x).strip() for x in src if str(x).strip())
            elif isinstance(src, str):
                custom_tags.extend(x.strip() for x in re.split(r'[,;#]+', src) if x.strip())

        # 2. Trích xuất thực thể chủ đề nổi bật TRỰC TIẾP TỪ TIÊU ĐỀ (đúng ngôn ngữ kênh EN vs pt-BR)
        KNOWN_SUBJECTS_EN = [
            "honey badger", "komodo dragon", "sperm whale", "colossal squid", "giant squid",
            "orca", "killer whale", "great white shark", "saltwater crocodile", "nile crocodile",
            "nile hippo", "hippo", "polar bear", "grizzly bear", "siberian tiger", "bengal tiger",
            "snow leopard", "jaguar", "leopard", "cheetah", "lion", "lions", "hyena", "harpy eagle",
            "howler monkey", "secretary bird", "water buffalo", "cassowary", "king cobra",
            "black mamba", "wolverine", "electric eel", "mantis shrimp", "pistol shrimp",
            "sloth", "sloths", "walrus", "giant otter", "otter", "shark", "crocodile",
            "blind dog", "shelter puppy", "stray kitten", "orphan puppy", "wheelchair dog", "puppy", "kitten", "dog",
            "mariana trench", "bermuda triangle", "boiling river", "amazon river", "amazon",
            "point nemo", "spacecraft graveyard", "saturation divers", "divers",
            "antarctica", "sahara", "krakatoa", "yellowstone", "pompeii", "roman empire", "rome",
            "steam engine", "titanic", "cold war", "world war 1", "world war 2", "ww2", "ww1",
            "ancient egypt", "giza", "alexandria", "alexander", "emperor qin",
            "chernobyl", "tsunami", "lituya bay", "alaska", "goldsboro", "north carolina",
            "tambora", "tunguska", "vesuvius", "hiroshima", "hindenburg", "habakkuk",
            "panama canal", "emu war", "tsar bomba", "nuclear", "nuke", "nukes", "b52",
            "napoleon", "genghis khan", "vikings", "sparta", "pearl harbor", "d day",
            "soviet", "rockefeller", "apollo", "spacecraft", "kraken", "abyss", "deep ocean",
            "body language", "eye contact", "fbi", "lie detector", "lie detection", "micro expressions",
            "narcissist", "sociopath", "psychopath", "gaslighting", "gaslighters", "love bombed", "manipulators",
            "manipulating", "mind control", "serial killers", "negotiator", "liars", "liar", "fake smile",
            "silence", "envy", "anger", "loneliness", "confidence", "confident",
        ]
        KNOWN_SUBJECTS_PT = [
            "onça pintada", "onça", "sucuri", "jacaré açu", "jacaré", "poraquê", "ariranha", "lobo guará",
            "tatu canastra", "águia harpia", "gavião real", "harpia", "bicho preguiça", "preguiça",
            "lagosta boxeadora", "tamarutaca", "dragão de komodo", "komodo", "hipopótamo", "crocodilo",
            "tubarão branco", "tubarão", "orca", "baleia", "cachalote", "lula colossal",
            "hiena", "leopardo das neves", "leopardo", "texugo do mel", "ratel", "mangusto", "javali",
            "leões", "leão", "tigre siberiano", "tigre", "urso pardo", "urso polar", "cobra rei", "mamba negra",
            "taiga", "amazônia", "pantanal", "cerrado", "fossa das marianas", "triângulo das bermudas",
            "ponto nemo", "espaçonaves", "mergulhadores", "rio fervente", "caverna movile", "caverna",
            "oceano", "navios", "ilha", "silêncio", "gelo", "rússia",
        ]
        subject_pool = KNOWN_SUBJECTS_PT if is_br else KNOWN_SUBJECTS_EN
        title_lower = re.sub(r'[\-_]+', ' ', base_title.lower())
        topic_lower = re.sub(r'[\-_]+', ' ', f"{base_title} {topic or ''}".lower())
        extracted_topic_tags: List[str] = []
        topic_hashtag_candidates: List[str] = []
        for subj in subject_pool:
            subj_norm = re.sub(r'[\-_]+', ' ', subj.lower()).strip()
            if re.search(rf'\b{re.escape(subj_norm)}\b', topic_lower):
                extracted_topic_tags.append(subj_norm)
                h_subj = re.sub(r'[^a-z0-9à-ỹçãõáéíóúâêô]+', '', subj_norm)
                if 3 <= len(h_subj) <= 18 and h_subj not in topic_hashtag_candidates:
                    topic_hashtag_candidates.append(h_subj)

        # Khử các tag thực thể động vật khác không xuất hiện nguyên từ trong tiêu đề khỏi custom_tags
        ALL_ANIMAL_ENTITIES = set(KNOWN_SUBJECTS_EN + KNOWN_SUBJECTS_PT)
        filtered_custom_tags: List[str] = []
        for ct in custom_tags:
            ct_low = re.sub(r'^#+', '', ct.strip()).lower()
            if ct_low in ALL_ANIMAL_ENTITIES and not re.search(rf'\b{re.escape(ct_low)}\b', title_lower):
                continue
            if is_br and ct_low in KNOWN_SUBJECTS_EN and ct_low not in KNOWN_SUBJECTS_PT:
                continue
            filtered_custom_tags.append(ct)
        custom_tags = filtered_custom_tags

        # 3. Hợp nhất danh sách Tags chuẩn cho YouTube (snippet.tags, tối đa 24 tags & < 450 ký tự)
        # Ưu tiên: Thực thể chủ đề (extracted_topic_tags) + Bộ tag chuẩn Ngách & Quốc gia (niche_core_tags) trước,
        # sau đó mới đến custom_tags đã qua bộ lọc để KHÔNG BAO GIỜ bị đẩy mất tag định hướng quốc gia ('usa'/'brasil')!
        final_tags: List[str] = []
        seen_lower = set()
        total_chars = 0

        for candidate in (*extracted_topic_tags, *niche_core_tags, *custom_tags):
            clean_t = re.sub(r'^#+', '', str(candidate or "").strip()).strip()
            if not _is_tag_allowed(clean_t):
                continue
            low_t = clean_t.lower()
            if is_long_form and low_t in ("shorts", "ytshorts", "youtubeshorts"):
                continue
            if low_t in seen_lower:
                continue
            if len(final_tags) >= 24 or (total_chars + len(clean_t) + 2) > 450:
                break
            final_tags.append(clean_t)
            seen_lower.add(low_t)
            total_chars += len(clean_t) + 2

        # 4. Xây dựng chuỗi Hashtag cho Tiêu đề (#Shorts + 2-3 hashtag chuẩn ngách/chủ đề cho Shorts, hoặc tiêu đề sạch cho Video Dài 16:9)
        if is_long_form:
            enriched_title = base_title[:100].rstrip()
            built_hashes: List[str] = []
            desc_hash_tokens: List[str] = ["#documentary"]
        else:
            title_hash_list: List[str] = ["Shorts"]
            for h_cand in (*topic_hashtag_candidates[:1], *niche_title_hashes):
                h_clean = re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', h_cand).lower()
                if h_clean and h_clean != "shorts" and h_clean not in [x.lower() for x in title_hash_list]:
                    title_hash_list.append(h_clean)
                if len(title_hash_list) >= 4:
                    break

            enriched_title = f"{base_title[:91].rstrip()} #Shorts"
            cur_title = base_title
            built_hashes = ["#Shorts"]
            for extra_h in title_hash_list[1:]:
                candidate_suffix = " ".join(built_hashes + [f"#{extra_h}"])
                if len(cur_title) + 1 + len(candidate_suffix) <= 100:
                    built_hashes.append(f"#{extra_h}")
            enriched_title = f"{cur_title} {' '.join(built_hashes)}".strip()
            if len(enriched_title) > 100:
                enriched_title = f"{base_title[:91].rstrip()} #Shorts"
            desc_hash_tokens = ["#Shorts"]

        # 5. Xây dựng khối Hashtags đầy đủ (8-10 thẻ) cho cuối phần Mô tả (Description)
        for h_tok in (*topic_hashtag_candidates, *niche_title_hashes, *[re.sub(r'\s+', '', t.lower()) for t in final_tags]):
            h_c = re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', str(h_tok)).lower()
            if not h_c or len(h_c) < 3 or len(h_c) > 24:
                continue
            if is_long_form and h_c in ("shorts", "ytshorts", "youtubeshorts"):
                continue
            tag_str = f"#{h_c}"
            if tag_str.lower() not in [x.lower() for x in desc_hash_tokens]:
                desc_hash_tokens.append(tag_str)
            if len(desc_hash_tokens) >= 10:
                break

        return {
            "base_title": base_title,
            "enriched_title": enriched_title,
            "title_hashtags": built_hashes,
            "description_hashtags": " ".join(desc_hash_tokens),
            "tags": final_tags,
        }

    def add_tags_to_video(
        self,
        video_id: str,
        new_tags: Any = None,
        append: bool = True,
        update_title_hashtags: bool = True,
        update_description_hashtags: bool = True,
        topic: str = "",
    ) -> Dict[str, Any]:
        """
        Gắn thêm (hoặc cập nhật) bộ Tags & Hashtags trực tiếp lên video đã có trên YouTube
        (áp dụng được cho cả video đang hẹn giờ `SCHEDULED/private` lẫn video đã công chiếu `PUBLIC`).
        """
        if not self.service or not video_id:
            return {"success": False, "error": "Chưa kết nối YouTube API hoặc thiếu video_id"}
        try:
            vres = self.service.videos().list(id=video_id, part="snippet").execute()
            items = vres.get("items", [])
            if not items:
                return {"success": False, "error": f"Không tìm thấy video {video_id} trên YouTube"}

            snip = items[0].get("snippet", {})
            cur_title = snip.get("title", "")
            cur_desc = snip.get("description", "")
            cur_tags = list(snip.get("tags") or [])

            input_tags: List[str] = []
            if isinstance(new_tags, list):
                input_tags = [str(t).strip() for t in new_tags if str(t).strip()]
            elif isinstance(new_tags, str):
                input_tags = [t.strip() for t in re.split(r'[,;#]+', new_tags) if t.strip()]

            combined_seed = (input_tags + cur_tags) if append else input_tags
            smart = self.build_smart_video_tags_and_hashtags(
                title=cur_title,
                topic=topic or cur_title,
                extra_tags=combined_seed,
            )

            # Đảm bảo toàn bộ tag do người dùng chủ động gõ vào luôn đứng đầu danh sách
            merged_tags: List[str] = []
            seen_l = set()
            total_c = 0
            for t_item in (*input_tags, *smart["tags"]):
                c_t = re.sub(r'^#+', '', str(t_item).strip()).strip()
                if not c_t or c_t.lower() in seen_l:
                    continue
                if len(merged_tags) >= 25 or (total_c + len(c_t) + 2) > 460:
                    break
                merged_tags.append(c_t)
                seen_l.add(c_t.lower())
                total_c += len(c_t) + 2

            snip["tags"] = merged_tags

            if update_title_hashtags:
                if append and 1 <= len(input_tags) <= 5:
                    base_t = smart["base_title"]
                    user_short_hashes = [
                        re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', t).lower()
                        for t in input_tags
                        if 2 <= len(re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', t)) <= 18
                    ]
                    hash_chain = ["#Shorts"]
                    for h_c in (*user_short_hashes[:2], *[h.lstrip("#") for h in smart["title_hashtags"][1:]]):
                        if h_c and h_c.lower() != "shorts" and f"#{h_c.lower()}" not in [x.lower() for x in hash_chain]:
                            cand_str = " ".join(hash_chain + [f"#{h_c.lower()}"])
                            if len(base_t) + 1 + len(cand_str) <= 100 and len(hash_chain) < 4:
                                hash_chain.append(f"#{h_c.lower()}")
                    snip["title"] = f"{base_t} {' '.join(hash_chain)}".strip()[:100]
                else:
                    snip["title"] = smart["enriched_title"]

            if update_description_hashtags:
                desc_body = re.sub(r'(?:\n*\s*(?:#[^\s#]+\s*)+)$', '', (cur_desc or "").strip()).strip()
                extra_desc_hashes = [
                    f"#{re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', t).lower()}"
                    for t in merged_tags[:10]
                    if len(re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', t)) >= 3
                ]
                all_desc_hashes: List[str] = ["#Shorts"]
                for dh in (*extra_desc_hashes, *smart["description_hashtags"].split()):
                    if dh.lower() not in [x.lower() for x in all_desc_hashes] and len(all_desc_hashes) < 12:
                        all_desc_hashes.append(dh)
                hash_block = " ".join(all_desc_hashes)
                snip["description"] = f"{desc_body}\n\n{hash_block}".strip() if desc_body else hash_block

            self.service.videos().update(
                part="snippet",
                body={
                    "id": video_id,
                    "snippet": snip,
                },
            ).execute()

            logger.info(
                f"🏷️ [PublisherAgent] Đã gắn {len(merged_tags)} tags lên video YouTube {video_id} ({self.channel_name}): "
                f"title='{snip.get('title')}' | tags={merged_tags[:8]}..."
            )
            return {
                "success": True,
                "video_id": video_id,
                "title": snip.get("title"),
                "description": snip.get("description"),
                "tags": merged_tags,
            }
        except Exception as e:
            logger.warning(f"🏷️ [PublisherAgent] Lỗi khi gắn thêm tag cho video {video_id}: {e}")
            return {"success": False, "error": str(e)}

    def sanitize_shorts_description(
        self,
        raw_desc: str,
        base_title: str = "",
        allow_affiliate: bool = False,
    ) -> str:
        """
        Làm sạch mô tả Shorts (Description Hygiene):
        1. Loại bỏ dòng tiêu đề bị lặp ở đầu và khối mẫu cứng `BREAKDOWN:` / `ANÁLISE DO ...:` + các gạch đầu dòng `• ...` gây đội ký tự lên 700-850 chars.
        2. Nếu `allow_affiliate=False`, gỡ bỏ hoàn toàn các khối chứa link ngoài (`https://www.amazon.com/...`, `paypal.me/...`, `🛒 Recommended:`).
        3. Giữ lại đoạn tóm tắt tự nhiên ngắn gọn (150-300 ký tự) trước khi gắn khối Hashtag chuẩn ngách.
        """
        if not raw_desc:
            return ""
        text = re.sub(r'(?:\n*\s*(?:#[^\s#]+\s*)+)$', '', str(raw_desc).strip()).strip()
        if not text:
            return ""

        clean_base_title = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', str(base_title or "").strip()).strip().lower()
        lines = [ln.strip() for ln in text.splitlines()]
        filtered_lines: List[str] = []

        breakdown_headers = (
            "⏳ alternate history timeline breakdown:",
            "🌊 scientific & mystery breakdown:",
            "🌊 análise do mistério e ciência:",
            "🦈 apex wildlife breakdown:",
            "🐆 análise do confronto selvagem:",
            "🐾 rescue transformation story:",
            "🧠 key psychological breakdown:",
            "📌 key video breakdown:",
            "🧠 3 điểm cốt lõi trong video:",
        )

        for ln in lines:
            if not ln:
                continue
            ln_low = ln.lower()
            # 1. Bỏ dòng lặp lại nguyên văn tiêu đề ở đầu mô tả
            ln_no_hash = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', ln).strip().lower()
            if clean_base_title and ln_no_hash == clean_base_title:
                continue
            # 2. Bỏ dòng hook có icon ⚡ và các dòng header/bullet của mẫu BREAKDOWN cứng
            if ln.startswith("⚡ "):
                continue
            if any(ln_low.startswith(bh) for bh in breakdown_headers):
                continue
            if ln.startswith("• ") or ln.startswith("1️⃣ ") or ln.startswith("2️⃣ ") or ln.startswith("3️⃣ "):
                continue
            # 3. Bỏ các dòng chứa link ngoài hoặc CTA Affiliate/PayPal khi kênh tắt monetization
            if not allow_affiliate:
                if any(
                    tok in ln_low
                    for tok in (
                        "http://",
                        "https://",
                        "amazon.com",
                        "paypal.me",
                        "🛒 recommended",
                        "👉 check it out here",
                        "☕ enjoying these",
                        "support the channel:",
                    )
                ):
                    continue
            filtered_lines.append(ln)

        cleaned_blocks: List[str] = []
        chapter_buffer: List[str] = []
        for ln in filtered_lines:
            is_chap_line = bool(
                re.match(r"^\d{1,2}:\d{2}\s+", ln)
                or ln.lower().startswith(("⏱️ chapters", "⏱️ capítulos", "chapters:", "capítulos:"))
            )
            if is_chap_line:
                chapter_buffer.append(ln)
            else:
                if chapter_buffer:
                    cleaned_blocks.append("\n".join(chapter_buffer))
                    chapter_buffer = []
                cleaned_blocks.append(ln)
        if chapter_buffer:
            cleaned_blocks.append("\n".join(chapter_buffer))

        cleaned = "\n\n".join(cleaned_blocks).strip()
        if not cleaned and base_title:
            cleaned = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', str(base_title).strip()).strip()
        return cleaned

    def build_youtube_chapters_from_scenes(
        self,
        scenes: List[Any],
        language: str = "en",
    ) -> str:
        """Ủy quyền sang hàm module-level `build_youtube_chapters_from_scenes`."""
        return build_youtube_chapters_from_scenes(scenes=scenes, language=language)


def build_youtube_chapters_from_scenes(
    scenes: List[Any],
    language: str = "en",
) -> str:
    """
    Tự động tính toán và sinh khối YouTube Chapters chuẩn thuật toán (`0:00 Intro`, `0:42 ...`)
    từ danh sách phân cảnh (`scenes`) dựa trên `audio_duration_ms` thực tế của từng cảnh.
    - Học từ `@disastersexplained1` (`Ud7TKMKQrHo`) và `@FossilVideos` (`5QExdZCvdgE`):
      1. Bắt buộc bắt đầu bằng `0:00`.
      2. Tối thiểu 3 chương, mỗi chương cách nhau >= 10 giây.
      3. Giúp YouTube kích hoạt thanh chia chương tương tác (Interactive Chapter Scrubbing) và SEO Google Search Key Moments.
    """
    if not scenes or len(scenes) < 4:
        return ""

    def _get_field(sc: Any, key: str, default: Any = None) -> Any:
        if isinstance(sc, dict):
            return sc.get(key, default)
        return getattr(sc, key, default)

    # Tính thời điểm bắt đầu tích lũy (cumulative start seconds) của từng phân cảnh
    scene_starts: List[Tuple[int, float, Any]] = []
    cum_sec = 0.0
    for idx, sc in enumerate(scenes):
        scene_starts.append((idx, cum_sec, sc))
        dur_ms = _get_field(sc, "audio_duration_ms") or _get_field(sc, "duration_ms")
        if dur_ms and float(dur_ms) > 0:
            dur_s = float(dur_ms) / 1000.0
        else:
            dur_s = max(float(_get_field(sc, "estimated_seconds") or 4.5), 2.0)
        cum_sec += dur_s

    # Hỗ trợ Chapters cho cả Long-Form (>= 50s) và Shorts nhiều vòng / Gauntlet (>= 26s, >= 4 cảnh)
    if cum_sec < 26.0 and len(scenes) < 4:
        return ""
    min_gap_sec = 11.0 if cum_sec >= 55.0 else 7.5
    min_tail_sec = 8.0 if cum_sec >= 55.0 else 4.0

    is_pt = str(language or "").strip().lower().startswith("pt")
    is_vi = str(language or "").strip().lower().startswith("vi")
    header_label = "⏱️ Capítulos:" if is_pt else ("⏱️ Phân đoạn video:" if is_vi else "⏱️ Chapters:")
    intro_prefix = "Introdução" if is_pt else ("Mở đầu" if is_vi else "Intro")

    def _fmt_mm_ss(sec_val: float) -> str:
        total_s = max(0, int(round(sec_val)))
        mm = total_s // 60
        ss = total_s % 60
        return f"{mm}:{ss:02d}"

    _TRAILING_STOP_WORDS = {
        "the", "a", "an", "that", "which", "who", "whom", "whose", "when", "where", "why", "how",
        "and", "or", "but", "if", "because", "as", "until", "while", "of", "at", "by", "for", "with",
        "about", "against", "between", "into", "through", "during", "before", "after", "above", "below",
        "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
        "can", "could", "may", "might", "must", "your", "their", "our", "his", "her", "its", "my",
        "this", "these", "those", "massive", "giant", "huge", "extreme", "ultimate", "deadly", "raw",
        "true", "exact", "single", "pure", "total", "complete", "easily", "instantly", "literally", "nearly",
    }

    def _strip_dangling_words(words_list: List[str]) -> List[str]:
        cleaned = list(words_list)
        while len(cleaned) > 2 and (
            cleaned[-1].lower().strip(".,!?:;—–-'\"") in _TRAILING_STOP_WORDS
            or cleaned[-1].lower().strip(".,!?:;—–-'\"").endswith("ly")
        ):
            cleaned.pop()
        return cleaned

    def _extract_clean_chapter_label(sc: Any, chap_num: int, is_first: bool = False) -> str:
        for field_name in ("overlay_badge", "visual_reasoning"):
            raw_val = str(_get_field(sc, field_name) or "").strip()
            if raw_val and raw_val.lower() != "null":
                m_ch = re.search(r"(?:chapter|capítulo|round|phase)\s*\d+\s*[:\-–—]\s*([^—–\-\|]+)", raw_val, flags=re.IGNORECASE)
                if m_ch:
                    clean_b = m_ch.group(1).strip()
                    clean_b = re.sub(r"\b(?:visual intent|documentary beat)\b", "", clean_b, flags=re.IGNORECASE).strip(" :—–-")
                    if len(clean_b) >= 4:
                        title_b = clean_b.title()
                        return f"{intro_prefix} — {title_b}" if is_first else (f"Capítulo {chap_num}: {title_b}" if is_pt else f"Chapter {chap_num}: {title_b}")
                elif field_name == "overlay_badge" and 4 <= len(raw_val) <= 32:
                    title_b = raw_val.strip(" :—–-|").title()
                    return f"{intro_prefix} — {title_b}" if is_first else (f"Capítulo {chap_num}: {title_b}" if is_pt else f"Chapter {chap_num}: {title_b}")

        vk = str(_get_field(sc, "visual_keyword") or "").strip()
        if vk and vk.lower() != "null":
            vk_words = [
                w for w in vk.split()
                if w.lower() not in {"4k", "hd", "footage", "video", "clip", "cinematic", "realistic", "close", "up", "shot"}
            ]
            vk_clean = " ".join(_strip_dangling_words(vk_words[:4])).title()
            if len(vk_clean) >= 4:
                return f"{intro_prefix} — {vk_clean}" if is_first else (f"Capítulo {chap_num}: {vk_clean}" if is_pt else f"Chapter {chap_num}: {vk_clean}")

        narration = str(_get_field(sc, "narration") or "").strip()
        if narration:
            first_clause = re.split(r"[.!?;:—–\-]", narration)[0].strip()
            words = [w for w in re.findall(r"[\w'’-]+", first_clause) if w]
            words = _strip_dangling_words(words[:7])
            if words:
                snippet = " ".join(words).title()
                return f"{intro_prefix} — {snippet}" if is_first else (f"Capítulo {chap_num}: {snippet}" if is_pt else f"Chapter {chap_num}: {snippet}")

        return intro_prefix if is_first else (f"Capítulo {chap_num}" if is_pt else f"Chapter {chap_num}")

    # Ưu tiên các cảnh có gắn sẵn overlay_badge dạng CHAPTER / ROUND, hoặc chia đều 10 chương (cho video 10 phút) / 5 chương
    selected_indices: List[int] = [0]
    last_chap_sec = 0.0

    badge_candidates: List[int] = []
    for idx, st_sec, sc in scene_starts[1:]:
        b_raw = str(_get_field(sc, "overlay_badge") or "").strip()
        if b_raw and b_raw.lower() != "null" and any(k in b_raw.lower() for k in ("chapter", "capítulo", "round", "phase", "twist", "boss", "record")):
            badge_candidates.append(idx)

    if len(badge_candidates) >= 2:
        for b_idx in badge_candidates:
            st_sec = scene_starts[b_idx][1]
            if st_sec - last_chap_sec >= min_gap_sec and (cum_sec - st_sec) >= min_tail_sec:
                selected_indices.append(b_idx)
                last_chap_sec = st_sec

    min_req_chapters = 4 if cum_sec >= 55.0 else 3
    if len(selected_indices) < min_req_chapters:
        selected_indices = [0]
        last_chap_sec = 0.0
        target_chapters = 10 if len(scenes) >= 32 else (5 if len(scenes) >= 12 else 4)
        max_chapters_cap = 10 if len(scenes) >= 32 else 6
        step = max(2, len(scenes) // target_chapters)
        for idx in range(step, len(scenes) - 1, step):
            st_sec = scene_starts[idx][1]
            if st_sec - last_chap_sec >= min_gap_sec and (cum_sec - st_sec) >= min_tail_sec:
                selected_indices.append(idx)
                last_chap_sec = st_sec
                if len(selected_indices) >= max_chapters_cap:
                    break

    if len(selected_indices) < 3:
        return ""

    chapter_lines = [header_label]
    for c_num, s_idx in enumerate(selected_indices, start=1):
        _, st_sec, sc = scene_starts[s_idx]
        ts_str = "0:00" if s_idx == 0 else _fmt_mm_ss(st_sec)
        label = _extract_clean_chapter_label(sc, chap_num=c_num, is_first=(s_idx == 0))
        chapter_lines.append(f"{ts_str} {label}")

    return "\n".join(chapter_lines)

