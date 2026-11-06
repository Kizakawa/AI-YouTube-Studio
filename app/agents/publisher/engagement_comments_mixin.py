"""app.agents.publisher.engagement_comments_mixin — Sinh & ghim bình luận tương tác (Pinned Comment) tự động."""
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


class EngagementCommentsMixin:
    @staticmethod
    def _has_external_non_youtube_link(text: Optional[str]) -> bool:
        """Kiểm tra xem chuỗi có chứa URL ngoài YouTube (affiliate/spam) hay không. Cho phép link nội bộ youtu.be / youtube.com."""
        raw = str(text or "").strip()
        if not raw:
            return False
        if any(k in raw.lower() for k in ("amazon.com", "paypal.me", "amzn.to", "bit.ly")):
            return True
        urls = re.findall(r'https?://[^\s\)\]>]+', raw, flags=re.IGNORECASE)
        for u in urls:
            u_low = u.lower()
            if not any(yt_dom in u_low for yt_dom in ("youtu.be/", "youtube.com/watch", "youtube.com/shorts")):
                return True
        return False

    def sanitize_engagement_comment(self, comment_text: Optional[str]) -> str:
        """Khử sạch đuôi quảng cáo công nghiệp ('🔔 INSCREVA-SE...', '🔔 SUBSCRIBE...', '🔔 ĐĂNG KÝ...') khỏi bình luận."""
        raw_c = str(comment_text or "").strip()
        if not raw_c:
            return ""
        cleaned = re.sub(
            r'\s*(?:🔔|🐾|🌊|🦁)?\s*(?:inscreva-se|subscribe|đăng\s*ký)\b.*$',
            '',
            raw_c,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        cleaned = re.sub(
            r'\s+(?:e\s+inscreva-se(?:\s+no\s+canal)?|and\s+subscribe|&\s*subscribe)\b[^?!.]*',
            '',
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or raw_c
    def build_default_engagement_comment(self, title: str = "", target_lang: Optional[str] = None) -> str:
        """
        Tạo câu hỏi bình chọn tương tác (Binary Poll Comment) bám sát tiêu đề/chủ đề và khu vực của kênh:
        - Nếu tiêu đề có dạng đối đầu 'A vs B' (hoặc 'A x B'), tự động trích xuất (1) A hay (2) B.
        - Ngược lại, sinh câu hỏi bình chọn nhị phân (1) / (2) sát ngách và ngôn ngữ (en-US / pt-BR / vi-VN).
        """
        cfg_lang = str(target_lang or self.channel_config.get("language") or "en-US").lower().strip()
        is_vn = cfg_lang.startswith("vi")
        is_pt_br = cfg_lang.startswith("pt")
        raw_niche = (self.channel_config.get("niche") or "").lower()
        clean_t = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', str(title or "").strip()).strip()

        # Phát hiện cấu trúc đối đầu A vs B trong tiêu đề (hỗ trợ tên ghép có dấu gạch ngang & hậu tố emoji)
        clean_for_vs = re.sub(r'[^\w\sÀ-ỹçãõáéíóúâêô\-:\?!\|]', ' ', clean_t)
        clean_for_vs = re.sub(r'\s+', ' ', clean_for_vs).strip()
        # Nếu có dấu ':' ở đầu (ví dụ "Mordida de 1.500 PSI: Onça vs Jacaré"), ưu tiên vế chứa 'vs'
        for seg in clean_for_vs.split(":"):
            if re.search(r'\b(?:vs\.?|versus|x)\b', seg, flags=re.IGNORECASE):
                clean_for_vs = seg.strip()
                break
        vs_match = re.search(
            r'([A-Za-zÀ-ỹçãõáéíóúâêô\-]{2,25}(?:\s+[A-Za-zÀ-ỹçãõáéíóúâêô\-]{2,25}){0,2})\s+(?:vs\.?|versus|x)\s+([A-Za-zÀ-ỹçãõáéíóúâêô\-]{2,25}(?:\s+[A-Za-zÀ-ỹçãõáéíóúâêô\-]{2,25}){0,2})',
            clean_for_vs,
            flags=re.IGNORECASE,
        )
        if vs_match:
            side_a = vs_match.group(1).strip(" -")
            side_b = vs_match.group(2).strip(" -")
            if 2 <= len(side_a) <= 28 and 2 <= len(side_b) <= 28:
                if is_vn:
                    vn_vs_debates = [
                        f"Thật lòng đi: nếu là 2 {side_a} hợp sức đấu 1 {side_b} cùng lúc, kết quả có đảo chiều không? (1) Có hay (2) Không có cửa? 👇🔥",
                        f"Nếu tái đấu trong bóng tối hoàn toàn ở địa hình trung lập, phe nào mới sống sót bước ra? (1) {side_a} hay (2) {side_b}? 👇🔥",
                        f"Nếu {side_b} là kẻ tung đòn phục kích bất ngờ trước, {side_a} còn trụ nổi không? (1) Vẫn thắng hay (2) Gục ngay? 👇🔥",
                        f"Ở kích thước cực đại ngoài tự nhiên, ai mới thực sự là khắc tinh của kẻ kia? (1) {side_a} hay (2) {side_b}? 👇🔥",
                    ]
                    return vn_vs_debates[sum(ord(c) for c in (clean_t or "shorts")) % 4]
                if is_pt_br:
                    pt_vs_debates = [
                        f"Honestamente: e se fossem 2 {side_a} contra 1 {side_b} ao mesmo tempo, o resultado mudaria? (1) Sim ou (2) Sem chance? 👇🔥",
                        f"Se a revanche entre {side_a} e {side_b} acontecesse no escuro total, quem sairia vivo? (1) {side_a} ou (2) {side_b}? 👇🔥",
                        f"Seja sincero: se {side_b} atacasse primeiro de surpresa, {side_a} ainda sobreviveria? (1) Sim ou (2) Jamais? 👇🔥",
                        f"Em território neutro e no tamanho máximo, quem realmente domina essa revanche? (1) {side_a} ou (2) {side_b}? 👇🔥",
                    ]
                    return pt_vs_debates[sum(ord(c) for c in (clean_t or "shorts")) % 4]
                en_vs_debates = [
                    f"Be honest: if it were 2 {side_a} vs 1 {side_b} at the same time, does the outcome flip? (1) Yes or (2) No chance? 👇🔥",
                    f"If {side_a} and {side_b} rematched in pitch-black darkness, who walks away alive? (1) {side_a} or (2) {side_b}? 👇🔥",
                    f"If {side_b} landed the first surprise ambush, could {side_a} still survive? (1) Yes or (2) Never? 👇🔥",
                    f"On neutral ground at peak adult size, who truly takes the rematch? (1) {side_a} or (2) {side_b}? 👇🔥",
                ]
                return en_vs_debates[sum(ord(c) for c in (clean_t or "shorts")) % 4]

        rot_idx = sum(ord(c) for c in (clean_t or "shorts")) % 4

        if any(k in raw_niche for k in ("history", "what_if", "alternate_history")):
            if is_vn:
                return "Nếu là bạn ở thời khắc đó: Chọn (1) Giữ nguyên lịch sử hay (2) Thay đổi tất cả? Bình chọn 1 hoặc 2 bên dưới 👇"
            if is_pt_br:
                return "Se você estivesse lá: escolheria (1) Manter nossa linha do tempo ou (2) Reescrever a história? Comente 1 ou 2 abaixo! 👇"
            en_hist_polls = [
                "Quick Poll: Would you choose (1) Keep our timeline or (2) Rewrite history? Drop 1 or 2 below! 👇",
                "How would our world look today if this happened: (1) Better or (2) Total Chaos? Vote 1 or 2 below! 👇",
                "Could this historical turning point have been avoided: (1) YES or (2) NO? Drop your vote below! 👇",
                "Which outcome was more likely here: (1) Survival or (2) Collapse? Comment 1 or 2 below! 👇",
            ]
            return en_hist_polls[rot_idx]
        if "psychology" in raw_niche:
            if is_vn:
                return "Bạn đã từng gặp kiểu người này ngoài đời thực chưa? Gõ (1) Đã từng gặp hoặc (2) Chưa bao giờ bên dưới nhé 👇"
            if is_pt_br:
                return "Você já lidou com alguém assim na vida real? Comente (1) SIM ou (2) NÃO abaixo! 👇"
            en_psych_polls = [
                "Have you ever dealt with someone like this in real life? Drop (1) YES or (2) NO in the comments! 👇",
                "Would you spot this psychological tactic in real time: (1) Immediately or (2) Too Late? Comment 1 or 2! 👇",
                "Have you ever used this behavioral rule yourself? Vote (1) YES or (2) Not Yet below! 👇",
                "Do you think people do this on purpose: (1) Calculated or (2) Subconscious? Drop 1 or 2 below! 👇",
            ]
            return en_psych_polls[rot_idx]
        if any(k in raw_niche for k in ("rescue", "animal", "pet")):
            return (
                "Chấm điểm màn lột xác của bé từ 1 đến 10 nhé! 🥺 Thả ❤️ bên dưới để chúc bé luôn hạnh phúc!"
                if is_vn else (
                    "Dê uma nota de 1 a 10 para essa transformação emocionante! 🥺 Deixe um ❤️ nos comentários!"
                    if is_pt_br else
                    "Rate this sweet baby's transformation from 1 to 10! 🥺 Drop a ❤️ below if this made your day!"
                )
            )
        if any(k in raw_niche for k in ("predator", "wildlife")):
            if is_vn:
                return "Kèo sinh tử này bạn chọn phe nào? Gõ (1) hay (2) để bình chọn bên dưới 👇"
            if is_pt_br:
                pt_wild_polls = [
                    "Na sua opinião, algum predador sobreviveria a esse ataque: (1) SIM ou (2) Impossível? Deixe seu voto abaixo! 👇🔥",
                    "Qual arma natural é mais letal na natureza: (1) Força Bruta ou (2) Velocidade Mortal? Comente 1 ou 2 abaixo! 👇🔥",
                    "Você teria coragem de chegar perto desse animal na natureza: (1) Jamais ou (2) De longe? Comente abaixo! 👇🔥",
                    "Quem realmente domina o topo da cadeia alimentar aqui: (1) O Caçador ou (2) O Rival? Vote 1 ou 2 nos comentários! 👇🔥",
                ]
                return pt_wild_polls[rot_idx]
            en_wild_polls = [
                "Who takes the crown in this survival showdown: (1) Predator or (2) Challenger? Drop 1 or 2 below! 👇🔥",
                "Could any land predator survive a direct strike from this beast: (1) YES or (2) NO? Vote below! 👇🔥",
                "What wins in the wild: (1) Raw Bite Force or (2) Lethal Speed? Drop 1 or 2 in the comments! 👇🔥",
                "Is this the most fearless creature on Earth: (1) Undefeated or (2) Overrated? Comment 1 or 2 below! 👇🔥",
            ]
            return en_wild_polls[rot_idx]
        if any(k in raw_niche for k in ("deep_sea", "ocean", "abyss", "mariana", "mystery", "earth")):
            if is_vn:
                return "Theo bạn đâu là thử thách đáng sợ hơn dưới đáy biển sâu: (1) Áp suất nghiền nát hay (2) Bóng tối tuyệt đối? Bình chọn 1 hoặc 2 bên dưới 👇"
            if is_pt_br:
                pt_myst_polls = [
                    "Você teria coragem de descer até essa profundidade extrema: (1) SIM ou (2) JAMAIS? Comente 1 ou 2 abaixo! 👇🌊",
                    "O que é mais assustador no fundo do oceano: (1) A Pressão Esmagadora ou (2) O Escuro Absoluto? Vote 1 ou 2 nos comentários! 👇🌊",
                    "Até onde a ciência deve explorar o abismo: (1) Investigar Tudo ou (2) Deixar Intocado? Comente 1 ou 2 abaixo! 👇🌊",
                    "Qual mistério do oceano mais intriga você: (1) Predadores Abissais ou (2) Zonas Mortais? Deixe seu voto abaixo! 👇🌊",
                ]
                return pt_myst_polls[rot_idx]
            en_myst_polls = [
                "Would you ever risk descending into this extreme deep-sea zone: (1) YES or (2) NEVER? Drop 1 or 2 below! 👇🌊",
                "What is more terrifying in the deep ocean: (1) Crushing Pressure or (2) Pitch-Black Darkness? Vote 1 or 2 in the comments! 👇🌊",
                "Should scientists keep exploring these lethal ocean depths: (1) Explore Everything or (2) Leave It Untouched? Comment 1 or 2! 👇🌊",
                "Which deep-sea force is deadlier: (1) Extreme Physics or (2) Abyssal Predators? Drop 1 or 2 below! 👇🌊",
            ]
            return en_myst_polls[rot_idx]
        return (
            "Nếu rơi vào tình huống này ngoài đời thực, bạn chọn (1) Đối mặt hay (2) Rút lui ngay lập tức? 👇🔥"
            if is_vn else (
                "Você já sabia disso antes de assistir? Comente (1) SIM ou (2) NÃO abaixo! 👇🔥"
                if is_pt_br else
                "Did you know this before watching? Drop (1) YES or (2) NO in the comments below! 👇🔥"
            )
        )

    def add_pinned_comment(
        self,
        video_id: str,
        comment_text: str,
        retry_on_spam: bool = True,
        retry_delay_sec: int = 5,
    ) -> Optional[Dict]:
        """
        Tự động đăng bình luận tương tác đầu luồng (Top-Level Author Engagement Comment) của chính chủ kênh lên video PUBLIC.
        Lưu ý kỹ thuật: YouTube Data API v3 chỉ hỗ trợ `commentThreads().insert` khi video đã `public` và không có endpoint
        để tự động bấm nút 'Ghim' (Pin) của YouTube Studio. Vì vậy câu hỏi này luôn đi kèm với Dòng 1 Description + Visual CTA Banner.
        """
        if not self.service or not video_id or not comment_text:
            return None

        def _do_comment() -> Optional[Dict]:
            body = {
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": self.sanitize_engagement_comment(comment_text).strip()
                        }
                    }
                }
            }
            return self.service.commentThreads().insert(part="snippet", body=body).execute()

        SPAM_ERRORS = ("forbidden", "403", "commentsDisabled", "processingFailure", "commentTextTooLong")
        PERMANENT_ERRORS = ("commentsDisabled", "commentTextTooLong", "videoNotFound")

        try:
            res = _do_comment()
            comment_id = res.get("id")
            logger.info(
                f"💬 [PublisherAgent] ✅ Đã đăng bình luận tác giả lên video {video_id}: "
                f"'{comment_text[:45]}...' (Comment ID: {comment_id})"
            )
            return res
        except Exception as e:
            err_str = str(e).lower()
            if any(p.lower() in err_str for p in PERMANENT_ERRORS):
                logger.warning(
                    f"💬 [PublisherAgent] Comment bị chặn vĩnh viễn trên video {video_id}: {e}"
                )
                if "commentsdisabled" in err_str:
                    return {"status": "comments_disabled", "video_id": video_id}
                return None
            if retry_on_spam and retry_delay_sec > 0 and any(p.lower() in err_str for p in SPAM_ERRORS):
                logger.warning(
                    f"💬 [PublisherAgent] Comment tạm thời bị chặn trên video {video_id}. "
                    f"Tự động thử lại sau {retry_delay_sec} giây... ({e})"
                )
                time.sleep(retry_delay_sec)
                try:
                    res = _do_comment()
                    comment_id = res.get("id")
                    logger.info(
                        f"💬 [PublisherAgent] ✅ Retry thành công! Đã đăng comment tác giả (Comment ID: {comment_id})"
                    )
                    return res
                except Exception as e2:
                    logger.warning(f"💬 [PublisherAgent] Retry comment vẫn thất bại cho video {video_id}: {e2}")
                    return None
            logger.warning(f"💬 [PublisherAgent] Không thể đăng bình luận tác giả lên video {video_id}: {e}")
            return None

    def ensure_pinned_comment(self, video_id: str, comment_text: Optional[str] = None, **kwargs) -> Optional[Dict]:
        """
        Kiểm tra và đăng bình luận tương tác của chính chủ kênh cho video nếu video đã ở trạng thái PUBLIC
        và tác giả chưa có bình luận nào trên video.
        Ưu tiên lấy `pinned_comment` đã sinh riêng cho kịch bản từ DB (`VideoJob.scenes_json`) hoặc từ Dòng 1 `💬` của Description.
        """
        if not self.service or not video_id:
            return None

        try:
            # 1. Kiểm tra trạng thái video
            v_res = self.service.videos().list(part="status,snippet,contentDetails", id=video_id).execute()
            items = v_res.get("items", [])
            if not items:
                logger.warning(f"💬 [PublisherAgent] Video {video_id} không tồn tại trên YouTube.")
                return None

            v_item = items[0]
            privacy_status = v_item.get("status", {}).get("privacyStatus", "public")
            if privacy_status.lower() != "public":
                logger.info(f"💬 [PublisherAgent] Video {video_id} đang ở trạng thái {privacy_status}. Chỉ đăng bình luận khi video đã PUBLIC.")
                return None

            # 2. Lấy thông tin kênh của chính mình để nhận diện author
            my_channel_id = None
            try:
                ch_me = self.service.channels().list(part="id", mine=True).execute()
                ch_items = ch_me.get("items", [])
                if ch_items:
                    my_channel_id = ch_items[0].get("id")
            except Exception:
                my_channel_id = self.channel_config.get("youtube_channel_id")

            # 3. Kiểm tra xem tác giả (kênh này) đã có bình luận nào trên video chưa
            has_author_comment = False
            allow_affiliate = bool(self.channel_config.get("enable_monetization") is True or self.channel_config.get("affiliate_enabled") is True)
            try:
                threads_res = self.service.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=20
                ).execute()
                for t in threads_res.get("items", []):
                    top_obj = t.get("snippet", {}).get("topLevelComment", {})
                    top_c = top_obj.get("snippet", {})
                    author_cid = top_c.get("authorChannelId", {}).get("value")
                    if my_channel_id and author_cid == my_channel_id:
                        c_text = str(top_c.get("textOriginal") or top_c.get("textDisplay") or "").lower()
                        if not allow_affiliate and self._has_external_non_youtube_link(c_text):
                            try:
                                self.service.comments().delete(id=top_obj.get("id")).execute()
                                logger.info(f"🧹 [PublisherAgent] Đã xóa bình luận dính link ngoài ({top_obj.get('id')}) trên video {video_id} để thay bằng bình luận tương tác sạch.")
                                continue
                            except Exception as del_err:
                                logger.debug(f"Không thể xóa comment {top_obj.get('id')}: {del_err}")
                        has_author_comment = True
                        break
            except Exception as chk_e:
                logger.debug(f"Không thể kiểm tra commentThreads của video {video_id}: {chk_e}")

            if has_author_comment:
                logger.info(f"💬 [PublisherAgent] Video {video_id} đã có bình luận của kênh tác giả. Bỏ qua.")
                return {"status": "already_exists", "video_id": video_id}

            # 4. Xác định nội dung bình luận tương tác chuẩn theo video cụ thể
            effective_comment = comment_text
            if not effective_comment:
                try:
                    from app.database.models import get_db, VideoJob
                    with get_db() as db:
                        vj = db.query(VideoJob).filter(VideoJob.youtube_video_id == video_id).order_by(VideoJob.id.desc()).first()
                        if vj and vj.scenes_json:
                            s_obj = json.loads(vj.scenes_json) if isinstance(vj.scenes_json, str) else vj.scenes_json
                            if isinstance(s_obj, dict) and s_obj.get("pinned_comment"):
                                effective_comment = str(s_obj.get("pinned_comment")).strip()
                except Exception:
                    pass

            if not effective_comment:
                v_desc = str(v_item.get("snippet", {}).get("description", "") or "")
                for ln in v_desc.splitlines():
                    ln_s = ln.strip()
                    if ln_s.startswith("💬"):
                        cand_c = ln_s.lstrip("💬").strip()
                        if len(cand_c) >= 15:
                            effective_comment = cand_c
                            break

            if effective_comment and not allow_affiliate and self._has_external_non_youtube_link(effective_comment):
                effective_comment = None

            if not effective_comment:
                v_title = v_item.get("snippet", {}).get("title", "")
                effective_comment = self.build_default_engagement_comment(title=v_title)

            dur_iso = v_item.get("contentDetails", {}).get("duration", "")
            m_dur = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_iso or "")
            dur_sec = (int(m_dur.group(1) or 0) * 3600 + int(m_dur.group(2) or 0) * 60 + int(m_dur.group(3) or 0)) if m_dur else 0
            clean_comment = self.sanitize_engagement_comment(effective_comment)
            if 0 < dur_sec <= 65 and "youtu.be/" not in clean_comment:
                long_promo = self._build_long_form_cross_promo_line(exclude_video_id=video_id)
                if long_promo:
                    clean_comment = f"{clean_comment}\n\n{long_promo}"

            # 5. Đăng bình luận tác giả
            return self.add_pinned_comment(video_id, clean_comment, retry_on_spam=True, retry_delay_sec=4)
        except Exception as e:
            logger.warning(f"💬 [PublisherAgent] Lỗi khi ensure_pinned_comment cho {video_id}: {e}")
            return None

    def _build_long_form_cross_promo_line(self, exclude_video_id: str = "") -> Optional[str]:
        """Tìm video dài 16:9 (dur >= 90s) đang PUBLIC mới nhất trên kênh để chèn link điều hướng từ Shorts."""
        if not self.service:
            return None
        try:
            cached = getattr(self, "_cached_public_long_video", None)
            if cached is None:
                ch_res = self.service.channels().list(part="contentDetails", mine=True).execute()
                ch_items = ch_res.get("items") or []
                if not ch_items:
                    self._cached_public_long_video = False
                    return None
                upl_id = ch_items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                pl_res = self.service.playlistItems().list(part="contentDetails,status", playlistId=upl_id, maxResults=35).execute()
                cand_ids = [
                    it["contentDetails"]["videoId"]
                    for it in (pl_res.get("items") or [])
                    if (it.get("status", {}).get("privacyStatus") or "").lower() == "public"
                ]
                found_long = False
                if cand_ids:
                    v_res = self.service.videos().list(part="snippet,contentDetails,status", id=",".join(cand_ids[:35])).execute()
                    for v in v_res.get("items") or []:
                        if (v.get("status", {}).get("privacyStatus") or "").lower() != "public":
                            continue
                        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v.get("contentDetails", {}).get("duration", "") or "")
                        ds = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0
                        if ds >= 90:
                            found_long = {"id": v["id"], "title": v.get("snippet", {}).get("title", ""), "duration_sec": ds}
                            break
                self._cached_public_long_video = found_long
                cached = found_long
            if not cached or not isinstance(cached, dict):
                return None
            if cached.get("id") == exclude_video_id:
                return None
            l_id = cached["id"]
            l_title = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', str(cached.get("title") or "")).strip()
            cfg_lang = str(self.channel_config.get("language") or "en-US").lower().strip()
            if cfg_lang.startswith("pt"):
                return f"🎬 Assista ao Documentário Completo em 1080p ({l_title}): https://youtu.be/{l_id}"
            if cfg_lang.startswith("vi"):
                return f"🎬 Xem trọn bộ Phim Tài Liệu 1080p ({l_title}): https://youtu.be/{l_id}"
            return f"🎬 Watch the Full 1080p Documentary ({l_title}): https://youtu.be/{l_id}"
        except Exception as e:
            logger.debug(f"_build_long_form_cross_promo_line err: {e}")
            return None

    def sync_channel_pinned_comments(
        self,
        limit: int = 15,
        max_videos: Optional[int] = None,
        max_new_comments: int = 6,
        **kwargs,
    ) -> List[Dict]:
        """
        Quét danh sách các video đã xuất bản gần nhất của kênh và tự động bổ sung bình luận mồi nếu còn thiếu.
        Sử dụng uploads playlist (tiết kiệm quota API: 1 unit).
        """
        if not self.service:
            return []

        results = []
        try:
            # Lấy uploads playlist ID
            ch_res = self.service.channels().list(part="contentDetails", mine=True).execute()
            items = ch_res.get("items", [])
            if not items and self.channel_id:
                ch_res = self.service.channels().list(part="contentDetails", id=self.channel_config.get("youtube_channel_id", self.channel_id)).execute()
                items = ch_res.get("items", [])

            if not items:
                logger.warning(f"💬 [PublisherAgent] Không lấy được thông tin kênh '{self.channel_name}' để sync comments.")
                return []

            uploads_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if not uploads_id:
                return []

            # Quét các video gần nhất
            pl_res = self.service.playlistItems().list(
                part="snippet,status",
                playlistId=uploads_id,
                maxResults=min(limit, 50)
            ).execute()

            for it in pl_res.get("items", []):
                vid = it.get("snippet", {}).get("resourceId", {}).get("videoId")
                v_title = it.get("snippet", {}).get("title", "")
                p_status = it.get("status", {}).get("privacyStatus", "public")
                if not vid or p_status.lower() != "public":
                    continue

                res = self.ensure_pinned_comment(vid)
                if res and res.get("id"):
                    results.append({
                        "video_id": vid,
                        "title": v_title,
                        "comment_id": res.get("id"),
                        "comment_text": res.get("snippet", {}).get("topLevelComment", {}).get("snippet", {}).get("textOriginal", "")
                    })
        except Exception as e:
            logger.warning(f"💬 [PublisherAgent] Lỗi khi sync_channel_pinned_comments ({self.channel_name}): {e}")

        return results
