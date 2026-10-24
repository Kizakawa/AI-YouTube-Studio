"""app.agents.publisher.metadata_healer_mixin — Kiểm toán, tự chữa lành Metadata video & đồng bộ từ khóa thương hiệu kênh."""
import os
import sys
import re
import json
import time
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from config.settings import settings
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)


class MetadataHealerMixin:
    def verify_and_heal_video_metadata(self, video_id: str, **kwargs) -> bool:
        """
        Tự động kiểm tra và sửa chữa (Self-Healing) metadata của video trên YouTube:
        - Nếu categoryId bị lệch khỏi resolve_locked_category_id() (đặc biệt nếu dính cat=27) -> Tự động đổi về chuẩn ('22' hoặc '15').
        - Tự động làm sạch hashtag sai ngách, gỡ link affiliate rò rỉ / mẫu BREAKDOWN cứng và gắn đầy đủ bộ Hashtags + Tags chuẩn ngách (`build_smart_video_tags_and_hashtags`).
        - Nếu thiếu defaultLanguage / defaultAudioLanguage hoặc recordingDetails.location (US/BR Geo-Lock) -> Tự động vá ngay.
        """
        if not self.service or not video_id:
            return False
        try:
            vres = self.service.videos().list(id=video_id, part="snippet,recordingDetails").execute()
            items = vres.get("items", [])
            if not items:
                return False
            vobj = items[0]
            snip = vobj.get("snippet", {})
            rec = vobj.get("recordingDetails", {}) or {}

            expected_cat = self.resolve_locked_category_id()
            cfg_lang = str(self.channel_config.get("language") or "").strip().lower()
            is_br = cfg_lang.startswith("pt")
            expected_lang = "pt-BR" if is_br else "en-US"
            expected_loc = "Sao Paulo, Brazil" if is_br else "New York, United States"

            needs_update = False
            raw_title = str(snip.get("title") or "").strip()
            cur_tags = list(snip.get("tags") or [])
            smart = self.build_smart_video_tags_and_hashtags(
                title=raw_title,
                topic=raw_title,
                extra_tags=cur_tags,
            )
            healed_title = smart["enriched_title"]
            if healed_title and healed_title != raw_title:
                logger.info(
                    f"🏷️ [SelfHealMetadata] Chuẩn hóa Hashtag tiêu đề video {video_id}: '{raw_title}' -> '{healed_title}'"
                )
                snip["title"] = healed_title
                needs_update = True

            if set(t.lower() for t in cur_tags) != set(t.lower() for t in smart["tags"]):
                snip["tags"] = smart["tags"]
                needs_update = True

            allow_affiliate = bool(
                self.channel_config.get("enable_monetization") is True
                or self.channel_config.get("affiliate_enabled") is True
            )
            raw_desc = str(snip.get("description") or "").strip()
            desc_body = self.sanitize_shorts_description(
                raw_desc=raw_desc,
                base_title=smart.get("base_title") or raw_title,
                allow_affiliate=allow_affiliate,
            )
            # Ưu tiên lấy câu hỏi bình luận (pinned_comment) sinh riêng cho kịch bản của video từ DB VideoJob
            db_pinned_q = None
            try:
                from app.database.models import get_db, VideoJob
                with get_db() as db:
                    vj = db.query(VideoJob).filter(VideoJob.youtube_video_id == video_id).order_by(VideoJob.id.desc()).first()
                    if vj and vj.scenes_json:
                        s_obj = json.loads(vj.scenes_json) if isinstance(vj.scenes_json, str) else vj.scenes_json
                        if isinstance(s_obj, dict) and s_obj.get("pinned_comment"):
                            cand_q = str(s_obj.get("pinned_comment")).strip()
                            if cand_q and not (not allow_affiliate and any(k in cand_q.lower() for k in ("http://", "https://", "amazon.com", "paypal.me"))):
                                db_pinned_q = cand_q.lstrip("💬").strip()
            except Exception:
                pass

            stale_generic_markers = (
                "(1) undiscovered species or (2) natural anomaly",
                "(1) criatura desconhecida ou (2) fenômeno geológico",
                "what is your thought on this? drop a comment below",
            )
            if desc_body and any(m in desc_body.lower() for m in stale_generic_markers):
                cleaned_lines = [
                    ln for ln in desc_body.splitlines()
                    if not (ln.strip().startswith("💬") and any(m in ln.lower() for m in stale_generic_markers))
                ]
                desc_body = "\n\n".join([ln.strip() for ln in cleaned_lines if ln.strip()]).strip()

            if "💬" not in (desc_body or ""):
                poll_q = db_pinned_q or self.build_default_engagement_comment(
                    title=smart.get("base_title") or raw_title,
                    target_lang=expected_lang,
                )
                if poll_q:
                    clean_q = poll_q.lstrip("💬").strip()
                    desc_body = f"💬 {clean_q}\n\n{desc_body}".strip() if desc_body else f"💬 {clean_q}"
            healed_desc = f"{desc_body}\n\n{smart['description_hashtags']}".strip() if desc_body else smart["description_hashtags"]
            if healed_desc != raw_desc:
                snip["description"] = healed_desc
                needs_update = True

            if str(snip.get("categoryId") or "") != expected_cat:
                logger.warning(
                    f"🛡️ [SelfHealMetadata] Video {video_id} ({self.channel_name}) đang có categoryId={snip.get('categoryId')} "
                    f"!= chuẩn '{expected_cat}' -> Tự động khôi phục về '{expected_cat}'!"
                )
                snip["categoryId"] = expected_cat
                needs_update = True

            if snip.get("defaultLanguage") != expected_lang or snip.get("defaultAudioLanguage") != expected_lang:
                snip["defaultLanguage"] = expected_lang
                snip["defaultAudioLanguage"] = expected_lang
                needs_update = True

            rec_loc = rec.get("location") or {}
            if rec_loc.get("latitude") is None or rec_loc.get("longitude") is None:
                rec = {
                    "locationDescription": expected_loc,
                    "location": {
                        "latitude": -23.5505 if is_br else 40.7128,
                        "longitude": -46.6333 if is_br else -74.0060,
                        "altitude": 0.0,
                    },
                }
                needs_update = True

            if needs_update:
                self.service.videos().update(
                    part="snippet,recordingDetails,localizations",
                    body={
                        "id": video_id,
                        "snippet": snip,
                        "recordingDetails": rec,
                        "localizations": {
                            expected_lang: {
                                "title": str(snip.get("title") or "")[:100],
                                "description": str(snip.get("description") or "")[:5000],
                            }
                        },
                    },
                ).execute()
                logger.info(
                    f"✅ [SelfHealMetadata] Đã tự động khóa chuẩn metadata & gắn {len(snip.get('tags', []))} tags cho video {video_id} ({self.channel_name}): "
                    f"title='{snip.get('title')}', cat={expected_cat}, lang={expected_lang}"
                )
                return True
        except Exception as e:
            logger.debug(f"[SelfHealMetadata] Bỏ qua kiểm tra video {video_id}: {e}")
        return False

    def build_smart_channel_keywords(self, extra_keywords: Any = None, max_chars: int = 485) -> str:
        """
        Xây dựng chuỗi Channel Keywords chuẩn YouTube Data API v3 (`brandingSettings.channel.keywords`):
        - Cụm từ nhiều từ được bọc trong dấu ngoặc kép `"..."`, từ đơn giữ nguyên cách nhau bởi khoảng trắng.
        - Tự động kết hợp `channel_keywords` từ `config/channels.yaml`, bộ từ khóa cốt lõi theo ngách (`niche` & `language`),
          và `extra_keywords` truyền vào.
        - Giới hạn cứng `<= max_chars` (mặc định 485/500 ký tự) để không bao giờ bị lỗi `invalidChannelKeywords`.
        """
        raw_niche = str(self.channel_config.get("niche") or "general").strip().lower()
        cfg_lang = str(self.channel_config.get("language") or "en").strip().lower()
        is_br = cfg_lang.startswith("pt")

        if any(k in raw_niche for k in ("wildlife", "predator")):
            if is_br:
                niche_defaults = [
                    "odisseia selvagem", "predadores", "onça pintada", "animais selvagens",
                    "vida selvagem", "natureza selvagem", "reino animal", "fatos animais",
                    "mundo animal", "documentário natureza", "batalha animal", "felinos",
                    "amazônia", "pantanal", "sucuri", "jacaré", "águia harpia", "leão",
                    "tigre siberiano", "predador", "curiosidades animais", "brasil", "shorts", "ytshorts",
                ]
            else:
                niche_defaults = [
                    "wild odyssey", "wildlife", "apex predators", "animal kingdom",
                    "nature documentary", "wildlife battle", "animal facts", "wild animals",
                    "big cats", "siberian tiger", "jaguar vs caiman", "lion vs hyena",
                    "honey badger", "king cobra", "snow leopard", "bite force",
                    "predator vs prey", "bbc earth", "national geographic", "wildlife shorts",
                    "usa", "united states", "american", "us shorts", "shorts", "ytshorts",
                ]
        elif "psych" in raw_niche:
            niche_defaults = [
                "psychology of the world", "psychology", "dark psychology",
                "human behavior", "body language", "psychology facts", "mindset",
                "subconscious mind", "manipulation tactics", "gaslighting", "narcissist",
                "emotional intelligence", "cognitive bias", "stoicism", "mind control",
                "social dynamics", "reverse psychology", "micro expressions",
                "usa", "united states", "american", "us shorts", "shorts", "ytshorts",
            ]
        elif any(k in raw_niche for k in ("deep_sea", "ocean", "mystery", "earth")):
            if is_br:
                niche_defaults = [
                    "mistérios do mundo", "oceano profundo", "fatos desconhecidos",
                    "curiosidades", "abismo", "criaturas marinhas", "anomalia", "ciência",
                    "mistérios do oceano", "fundo do mar", "fenômenos inexplicáveis",
                    "fatos científicos", "ponto nemo", "descobertas científicas",
                    "enigmas da terra", "mistérios sem explicação", "curiosidades do mundo",
                    "brasil", "shorts", "ytshorts",
                ]
            else:
                niche_defaults = [
                    "mysteries of the world", "deep sea mysteries", "ocean mysteries",
                    "unexplained mysteries", "earth mysteries", "science facts", "deep ocean",
                    "thalassophobia", "abyssal zone", "point nemo", "unexplained phenomena",
                    "scientific mysteries", "weird science", "earth anomalies",
                    "underwater discoveries", "strange facts", "bizarre nature", "ocean facts",
                    "usa", "united states", "american", "us shorts", "shorts", "ytshorts",
                ]
        elif any(k in raw_niche for k in ("history", "what_if", "alternate")):
            niche_defaults = [
                "alternate history", "what if", "what if history", "crazy history",
                "historical facts", "historical disasters", "biggest mistakes in history",
                "world history", "american history", "cold war", "nuclear test",
                "butterfly effect", "history documentary", "dark history", "bizarre history",
                "ancient history", "world war history", "history shorts",
                "usa", "united states", "american", "us shorts", "shorts", "ytshorts",
            ]
        elif any(k in raw_niche for k in ("rescue", "pet", "animal_rescue")):
            niche_defaults = [
                "paws and hearts rescue", "animal rescue", "dog rescue", "puppy rescue",
                "stray dog rescue", "abandoned puppy", "shelter dog", "cat rescue",
                "kitten rescue", "animal transformation", "before and after rescue",
                "saved animals", "wholesome animals", "heartwarming", "faith in humanity",
                "golden retriever", "stray dog", "pet rescue story", "thedodo",
                "usa", "united states", "american", "us shorts", "shorts", "ytshorts",
            ]
        else:
            niche_defaults = ["facts", "mystery", "documentary", "usa", "shorts", "ytshorts", "viral"]

        candidates: List[str] = []
        for src in (extra_keywords, self.channel_config.get("channel_keywords"), niche_defaults):
            if not src:
                continue
            if isinstance(src, list):
                candidates.extend(str(x).strip() for x in src if str(x).strip())
            elif isinstance(src, str):
                for m in re.finditer(r'"([^"]+)"|([^,\s;]+)', src):
                    tok = (m.group(1) or m.group(2) or "").strip()
                    if tok:
                        candidates.append(tok)

        formatted_tokens: List[str] = []
        seen_lower = set()
        cur_len = 0

        for kw in candidates:
            clean_kw = re.sub(r'["#]+', "", kw).strip()
            clean_kw = re.sub(r"\s+", " ", clean_kw)
            if not clean_kw or len(clean_kw) < 2:
                continue
            low_kw = clean_kw.lower()
            if low_kw in seen_lower:
                continue
            token_str = f'"{clean_kw}"' if " " in clean_kw else clean_kw
            added_len = len(token_str) + (1 if formatted_tokens else 0)
            if cur_len + added_len > max_chars:
                continue
            formatted_tokens.append(token_str)
            seen_lower.add(low_kw)
            cur_len += added_len

        return " ".join(formatted_tokens)

    def sync_channel_branding_keywords(self, extra_keywords: Any = None) -> Dict[str, Any]:
        """
        Đồng bộ trực tiếp bộ Channel Keywords (`brandingSettings.channel.keywords`) và quốc gia mục tiêu (`country`)
        lên YouTube Studio thông qua YouTube Data API v3 `channels().update`.
        """
        if not self.service:
            return {"success": False, "channel_id": self.channel_id, "error": self.init_error or "Chưa kết nối YouTube API"}
        try:
            res = self.service.channels().list(part="brandingSettings", mine=True).execute()
            items = res.get("items", [])
            if not items:
                return {"success": False, "channel_id": self.channel_id, "error": "Không tìm thấy kênh trên tài khoản OAuth này"}

            ch_item = items[0]
            yt_id = ch_item.get("id")
            bs = ch_item.get("brandingSettings", {}) or {}
            ch_bs = bs.get("channel", {}) or {}

            keywords_str = self.build_smart_channel_keywords(extra_keywords=extra_keywords)
            ch_bs["keywords"] = keywords_str

            raw_niche = str(self.channel_config.get("niche") or "general").strip().lower()
            if not str(ch_bs.get("description") or "").strip():
                if any(k in raw_niche for k in ("history", "what_if", "alternate")):
                    ch_bs["description"] = (
                        'Welcome to Alternate History & "What If?" — exploring the biggest turning points, '
                        "declassified military showdowns, historical disasters, and counterfactual timelines "
                        "that nearly rewrote our modern world. Subscribe for daily high-stakes history Shorts! 🏛️☢️🌍"
                    )
            if ch_bs.get("title") == 'Alternate History & "What If?':
                ch_bs["title"] = 'Alternate History & "What If?"'

            target_country = str(self.channel_config.get("target_country") or "").strip().upper()
            if not target_country:
                cfg_lang = str(self.channel_config.get("language") or "en").strip().lower()
                target_country = "BR" if cfg_lang.startswith("pt") else "US"
            ch_bs["country"] = target_country

            bs["channel"] = ch_bs
            # Chỉ giữ các trường hợp lệ khi gọi channels().update(part="brandingSettings")
            update_body = {
                "id": yt_id,
                "brandingSettings": {
                    "channel": ch_bs,
                },
            }
            self.service.channels().update(part="brandingSettings", body=update_body).execute()
            logger.info(
                f"✅ [ChannelKeywords] Đã cập nhật Channel Keywords cho '{self.channel_name}' ({yt_id}, {len(keywords_str)}/500 chars, country={target_country})"
            )
            return {
                "success": True,
                "channel_id": self.channel_id,
                "youtube_channel_id": yt_id,
                "channel_name": self.channel_name,
                "country": target_country,
                "keywords": keywords_str,
                "char_length": len(keywords_str),
            }
        except Exception as e:
            logger.error(f"❌ [ChannelKeywords] Lỗi cập nhật Channel Keywords cho '{self.channel_name}': {e}")
            return {"success": False, "channel_id": self.channel_id, "error": str(e)}
