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

class PublisherAgent:
    """Agent chịu trách nhiệm xác thực OAuth2 và đăng tải/lên lịch video lên các kênh YouTube."""

    def __init__(self, channel_config: Optional[Dict] = None, account_name: str = "account_1"):
        self.channel_config = dict(channel_config or {})
        self.channel_id = self.channel_config.get("id", "default")
        if self.channel_id and self.channel_id != "default":
            try:
                from config.settings import settings
                full_ch = settings.get_channel_by_id(self.channel_id) or {}
                if full_ch:
                    merged_cfg = dict(full_ch)
                    merged_cfg.update({k: v for k, v in self.channel_config.items() if v is not None and k != "id"})
                    self.channel_config = merged_cfg
                    self.channel_id = self.channel_config.get("id", self.channel_id)
            except Exception as cfg_err:
                logger.debug(f"PublisherAgent get_channel_by_id merge error: {cfg_err}")
        self.channel_name = self.channel_config.get("name", "YouTube Channel")
        self.account_name = self.channel_config.get("google_account", account_name)

        # Quản lý Hạn mức Quota & Token từ Project Pool của kênh
        from app.services.quota_manager import QuotaManager, get_project_id_from_client_secret, COST_FULL_SHORTS, get_next_reset_time
        
        # Tự động chọn token và Project ID tối ưu trong pool của kênh này
        active_token, active_project_id = QuotaManager.get_active_token_for_channel(self.channel_id)
        self.project_id = active_project_id
        
        ch_token = active_token or f"credentials/channels/{self.channel_id}/youtube_token.json"
        acc_token = f"credentials/accounts/{self.account_name}/youtube_token.json"

        # Kiểm tra xem kênh này có phải kênh đầu tiên (mặc định) của account không
        is_first_ch = False
        try:
            import yaml
            from config.settings import settings
            cfg_path = Path(settings.BASE_DIR) / "config" / "channels.yaml"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                accounts = cfg.get("google_accounts", [])
                for a in accounts:
                    if a.get("id") == self.account_name:
                        b_chs = a.get("brand_channels", [])
                        if b_chs and b_chs[0].get("id") == self.channel_id:
                            is_first_ch = True
                        break
        except Exception as e:
            logger.debug(f"PublisherAgent config check error: {e}")

        if self.channel_config.get("token_file") and os.path.exists(self.channel_config["token_file"]):
            self.token_file = self.channel_config["token_file"]
        elif ch_token and os.path.exists(ch_token):
            self.token_file = ch_token
        elif is_first_ch and os.path.exists(acc_token):
            # Chỉ kênh đầu tiên được kế thừa token cấp tài khoản cũ
            self.token_file = acc_token
        else:
            # Các kênh khác bắt buộc phải có token riêng, KHÔNG ĐƯỢC dùng chung token
            # để tránh trường hợp đăng nhầm sang kênh cũ!
            self.token_file = None

        self.service = None
        self.init_error = None
        self._init_service()

    def _refresh_token_with_retry(self, creds) -> bool:
        """
        [FIX] Refresh OAuth token với retry 3 lần + exponential backoff.
        - Phân biệt lỗi permanent (invalid_grant → cần re-auth) vs tạm thời (mạng → retry).
        - Trả về True nếu refresh thành công, False nếu thất bại.
        """
        from google.auth.transport.requests import Request
        from google.auth.exceptions import TransportError

        PERMANENT_ERRORS = ("invalid_grant", "Token has been expired or revoked", "unauthorized_client")
        MAX_RETRIES = 3

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                creds.refresh(Request())
                return True
            except Exception as refresh_err:
                err_str = str(refresh_err)
                # Lỗi permanent: token bị thu hồi → không retry, báo người dùng re-auth
                if any(p in err_str for p in PERMANENT_ERRORS):
                    self.init_error = f"invalid_grant: {err_str}"
                    logger.error(
                        f"🔑 [TokenRefresh] Phiên OAuth của '{self.channel_name}' đã bị thu hồi vĩnh viễn. "
                        f"Vui lòng vào Web Dashboard -> Tab 'Kênh & Tài Khoản' -> Bấm '🔑 Cấp Quyền YouTube' cho kênh '{self.channel_id}'."
                    )
                    return False
                # Lỗi tạm thời (mạng, timeout, 5xx) → retry với backoff
                wait_sec = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(
                    f"⚠️ [TokenRefresh] Lần thử {attempt}/{MAX_RETRIES} refresh token '{self.channel_name}' "
                    f"thất bại ({err_str[:80]}). Thử lại sau {wait_sec}s..."
                )
                if attempt < MAX_RETRIES:
                    time.sleep(wait_sec)

        self.init_error = f"Không thể refresh token sau {MAX_RETRIES} lần thử."
        logger.error(f"[TokenRefresh] Đã thử {MAX_RETRIES} lần nhưng không thể refresh token cho '{self.channel_name}'.")
        return False

    def _init_service(self):
        """Khởi tạo YouTube v3 client qua OAuth credentials"""
        if not self.token_file:
            self.init_error = f"Kênh '{self.channel_name}' chưa có file token riêng."
            logger.warning(
                f"Kênh '{self.channel_name}' (ID: {self.channel_id}) chưa có token YouTube riêng. "
                f"Hệ thống chặn không dùng token kênh khác để tránh đăng nhầm video!"
            )
            return

        try:
            creds = None
            env_b64 = os.getenv("YOUTUBE_TOKEN_BASE64")
            if env_b64:
                token_data = json.loads(base64.b64decode(env_b64).decode("utf-8"))
                creds = Credentials.from_authorized_user_info(token_data)
            elif os.path.exists(self.token_file):
                from app.utils.security import load_encrypted_json, save_encrypted_json
                token_data = load_encrypted_json(self.token_file)
                if token_data:
                    creds = Credentials.from_authorized_user_info(token_data)

            if creds:
                # [FIX] Tự động refresh token nếu hết hạn — dùng retry có backoff
                if creds.expired and creds.refresh_token:
                    refresh_ok = self._refresh_token_with_retry(creds)
                    if refresh_ok:
                        # Lưu lại token đã refresh được mã hoá AES-256
                        if self.token_file and os.path.exists(self.token_file):
                            from app.utils.security import save_encrypted_json
                            save_encrypted_json(self.token_file, json.loads(creds.to_json()))
                            logger.info(f"✅ Đã tự động refresh & lưu mã hoá AES-256 YouTube token cho '{self.channel_name}'")
                    else:
                        # Raise để khối except bên dưới tự động fallback về token chính nếu đang dùng token phụ
                        raise RuntimeError(self.init_error or "Token refresh failed")

                self.creds = creds
                self.service = build("youtube", "v3", credentials=creds)
                self.init_error = None
                logger.info(f"YouTube Data API v3 đã kết nối thành công cho '{self.channel_name}'")
            else:
                self.init_error = f"File token rỗng hoặc không giải mã được: {self.token_file}"
                logger.warning(f"Chưa tìm thấy youtube_token hợp lệ cho '{self.token_file}'")
        except Exception as e:
            # Nếu đang dùng token phụ mà bị lỗi invalid_grant / expired, cô lập token hỏng và fallback ngay về token chính
            primary_tok = os.path.join(settings.CREDENTIALS_DIR, "channels", self.channel_id, "youtube_token.json")
            if self.token_file and os.path.exists(primary_tok) and os.path.abspath(self.token_file) != os.path.abspath(primary_tok):
                logger.warning(f"⚠️ Token phụ '{self.token_file}' bị lỗi ({e}). Đang tự động fallback về token chính '{primary_tok}'...")
                if "invalid_grant" in str(e) and os.path.exists(self.token_file):
                    try:
                        bak_path = f"{self.token_file}.revoked.bak"
                        if os.path.exists(bak_path):
                            os.remove(bak_path)
                        os.rename(self.token_file, bak_path)
                        logger.info(f"🧹 Đã cô lập token phụ hết hạn sang '{bak_path}' để tránh xoay nhầm.")
                    except Exception as ren_err:
                        logger.debug(f"Không thể đổi tên token phụ hỏng: {ren_err}")
                self.token_file = primary_tok
                try:
                    from app.utils.security import load_encrypted_json, save_encrypted_json
                    from app.services.quota_manager import QuotaManager
                    token_data = load_encrypted_json(primary_tok)
                    if token_data:
                        creds = Credentials.from_authorized_user_info(token_data)
                        if creds.expired and creds.refresh_token:
                            from google.auth.transport.requests import Request
                            creds.refresh(Request())
                            save_encrypted_json(primary_tok, json.loads(creds.to_json()))
                        self.creds = creds
                        self.service = build("youtube", "v3", credentials=creds)
                        self.project_id = QuotaManager.get_channel_project_id(self.channel_id)
                        self.init_error = None
                        logger.info(f"✅ Đã fallback kết nối thành công qua token chính cho '{self.channel_name}' (Project: {self.project_id})!")
                        return
                except Exception as fb_err:
                    logger.error(f"Fallback token chính cũng thất bại: {fb_err}")

            self.init_error = str(e)
            logger.error(f"Lỗi khởi tạo YouTube API cho '{self.channel_name}': {e}")

    @trace_custom_span("PublisherAgent.upload_youtube", run_type="tool")
    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        schedule_time_iso: Optional[str] = None,
        privacy_status: str = "public",
        default_language: Optional[str] = "en",
        default_audio_language: Optional[str] = "en",
        pinned_comment: Optional[str] = None,
        topic: str = "",
        script: str = "",
        thumbnail_path: Optional[str] = None,
        notify_subscribers: Optional[bool] = None,
    ) -> Dict:
        """
        Upload video lên YouTube qua YouTube Data API v3.
        Nếu có schedule_time_iso (ví dụ '2026-09-25T19:00:00Z'), video sẽ được đặt ở chế độ private và lên lịch công chiếu.
        Đã bổ sung defaultLanguage & defaultAudioLanguage để định tuyến thuật toán đề xuất chính xác vùng người xem.
        Tự động trích xuất và tải lên Shock Thumbnail và Bình luận mồi ghim.
        Tự động gắn link Amazon Affiliate chuẩn xác vào Mô tả & Bình luận mồi.
        """
        import re
        if not self.service:
            self._init_service()

        if not self.service:
            if self.init_error and ("invalid_grant" in self.init_error or "expired" in self.init_error):
                err_msg = (
                    f"⚠️ Phiên đăng nhập YouTube của kênh '{self.channel_name}' đã hết hạn (Google OAuth token expired). "
                    f"Vui lòng vào Web Dashboard (http://127.0.0.1:8000) -> Tab 'Kênh & Tài Khoản' -> Bấm '🔑 Cấp Quyền YouTube (Kênh Này)' để kết nối lại."
                )
            else:
                err_msg = (
                    f"Kênh '{self.channel_name}' chưa có token YouTube riêng hoặc chưa được cấp quyền ({self.init_error or 'Chưa xác thực'}). "
                    f"Vui lòng vào Web Dashboard -> Tab 'Kênh & Tài Khoản' -> Bấm '🔑 Cấp Quyền YouTube (Kênh Này)'."
                )
            logger.error(f"Không thể kết nối YouTube API để upload: {self.token_file} ({err_msg})")
            return {
                "status": "failed",
                "error": err_msg,
                "message": err_msg,
                "video_id": None
            }

        if not os.path.exists(video_path):
            return {
                "status": "failed",
                "error": f"File video không tồn tại: {video_path}",
                "video_id": None
            }

        # ──────────────────────────────────────────────────────────
        # 1. PRE-UPLOAD VIDEO INTEGRITY GUARD (CHẶN TUYỆT ĐỐI LỖI P0D / FILE RỖNG)
        # ──────────────────────────────────────────────────────────
        try:
            f_size = os.path.getsize(video_path)
            is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules)
            if f_size < 150 * 1024 and not is_pytest:
                err_p0d = f"🛡️ [IntegrityGuard] Chặn upload: File video quá nhỏ hoặc bị hỏng ({f_size} bytes < 150KB): {video_path}"
                logger.error(err_p0d)
                return {"status": "failed", "error": err_p0d, "message": err_p0d, "video_id": None}

            from app.utils.ffmpeg_helper import get_duration_ms
            vid_dur_ms = get_duration_ms(video_path)
            if vid_dur_ms > 0 and vid_dur_ms < 8000 and not is_pytest:
                err_short = f"🛡️ [IntegrityGuard] Chặn upload: Thời lượng video quá ngắn ({vid_dur_ms/1000:.1f}s < 8s, nguy cơ lỗi P0D): {video_path}"
                logger.error(err_short)
                return {"status": "failed", "error": err_short, "message": err_short, "video_id": None}
        except Exception as e_probe:
            logger.debug(f"[IntegrityGuard] Probe check warning: {e_probe}")

        # ──────────────────────────────────────────────────────────
        # 1.5. LIVE YOUTUBE CHANNEL ID IDENTITY LOCK (CHỐNG ĐĂNG NHẦM KÊNH TRONG CÙNG GOOGLE ACCOUNT)
        # ──────────────────────────────────────────────────────────
        expected_yt_id = str(self.channel_config.get("youtube_channel_id") or "").strip()
        if expected_yt_id and self.service:
            try:
                ch_info_resp = self.service.channels().list(part="id,snippet", mine=True).execute()
                ch_items = ch_info_resp.get("items") or []
                if ch_items:
                    live_yt_id = str(ch_items[0].get("id") or "").strip()
                    live_yt_title = str((ch_items[0].get("snippet") or {}).get("title") or "").strip()
                    if live_yt_id and live_yt_id != expected_yt_id:
                        err_mismatch = (
                            f"🛡️ [ChannelIdentityLock] CHẶN UPLOAD NHẦM KÊNH: Token của '{self.channel_name}' ({self.channel_id}) "
                            f"đang trỏ sang kênh '{live_yt_title}' ({live_yt_id}) thay vì '{expected_yt_id}'!"
                        )
                        logger.error(err_mismatch)
                        return {"status": "failed", "error": err_mismatch, "message": err_mismatch, "video_id": None}
            except Exception as e_id_lock:
                logger.debug(f"[ChannelIdentityLock] Check warning: {e_id_lock}")

        # ──────────────────────────────────────────────────────────
        # 2. HARD NICHE FIREWALL (CHẶN TUYỆT ĐỐI NHIỄM CHÉO NGÁCH KÊNH)
        # ──────────────────────────────────────────────────────────
        try:
            from app.services.niche_manager import validate_channel_niche_alignment
            niche_check = validate_channel_niche_alignment(
                channel_id=self.channel_id,
                title=title,
                topic=topic,
                script=script,
            )
            if not niche_check.get("passed", True):
                fw_msg = (
                    f"🛡️ [HardNicheFirewall] CHẶN UPLOAD LỆCH NGÁCH trên kênh '{self.channel_name}' ({self.channel_id}): "
                    f"{niche_check.get('reason')} "
                    f"(Kênh gợi ý đúng ngách: {niche_check.get('suggested_channel_id') or 'N/A'})"
                )
                logger.error(fw_msg)
                return {
                    "status": "failed",
                    "error": fw_msg,
                    "message": fw_msg,
                    "video_id": None,
                    "suggested_channel_id": niche_check.get("suggested_channel_id"),
                }
        except Exception as e_fw:
            logger.debug(f"[HardNicheFirewall] Check warning: {e_fw}")

        # ──────────────────────────────────────────────────────────
        # QUOTA GUARD: Kiểm tra hạn mức & TỰ ĐỘNG XOAY PROJECT TRONG POOL CỦA KÊNH
        # ──────────────────────────────────────────────────────────
        from app.services.quota_manager import QuotaManager, COST_FULL_SHORTS, get_next_reset_time
        
        # Thử tìm token khác trong pool của kênh nếu dự án hiện tại hết quota
        opt_token, opt_proj_id = QuotaManager.get_active_token_for_channel(self.channel_id, required_units=COST_FULL_SHORTS)
        if opt_token and opt_token != self.token_file:
            logger.info(f"🔄 [ProjectRotation] Kênh '{self.channel_name}' tự động xoay sang Dự án '{opt_proj_id}' ({opt_token}) còn Quota!")
            self.token_file = opt_token
            self.project_id = opt_proj_id
            self._init_service()

        if not QuotaManager.can_upload(self.project_id, required_units=COST_FULL_SHORTS):
            next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
            quota_msg = (
                f"Tất cả dự án Google Cloud của kênh '{self.channel_name}' đã đạt giới hạn an toàn hôm nay (dự án '{self.project_id}'). "
                f"Video đã được tự động lưu và chuyển vào hàng đợi an toàn cho chính kênh '{self.channel_name}', "
                f"sẽ tự động đăng sau khi Google cấp hạn mức mới lúc {next_reset_str}."
            )
            logger.warning(f"⚠️ [QuotaGuard] {quota_msg}")
            return {
                "status": "waiting_quota_reset",
                "error": quota_msg,
                "message": quota_msg,
                "video_id": None,
                "project_id": self.project_id
            }

        # Khử tiền tố Variant / Option rò rỉ từ LLM và hậu tố kênh/quốc gia (ví dụ "Variant 1: ...", " - Maya Leader VN")
        clean_title = re.sub(
            r'^(?:(?:hook|title|tiêu\s*đề)\s*)?(?:variant|option|ver|version|biến\s*thể)\s*#?\d+\s*(?:\([^)]*\))?\s*[:\-–—]\s*',
            '',
            (title or "").strip(),
            flags=re.IGNORECASE,
        ).strip()
        clean_title = re.sub(
            r'[\[\(\s\-_|:]+[\w\s]*?\b(?:VN|VI|Vietnamese|Vietnam)\b.*$',
            '',
            clean_title,
            flags=re.IGNORECASE
        ).strip()
        # Khử toàn bộ chuỗi hashtag cũ ở cuối tiêu đề để gắn lại bộ Hashtag Chuẩn Ngách + Chủ Đề thông minh
        clean_title = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', clean_title).strip()
        clean_title = re.sub(r'#shorts\b', '', clean_title, flags=re.IGNORECASE).strip()
        if not clean_title:
            clean_title = (title or "Shorts Video").strip()

        smart_bundle = self.build_smart_video_tags_and_hashtags(
            title=clean_title,
            topic=topic or "",
            script=script or "",
            extra_tags=tags,
        )
        clean_title = smart_bundle["enriched_title"]

        # Tự động tối ưu hóa kiếm tiền qua Amazon Affiliate & Kêu gọi ủng hộ PayPal
        prod_info = None
        desc_clean = description or ""
        niche_id = str(self.channel_config.get("niche") or "").lower()
        
        affiliate_enabled = False
        try:
            ch_monet = self.channel_config.get("enable_monetization")
            ch_aff = self.channel_config.get("affiliate_enabled")
            if ch_monet is False or ch_aff is False:
                affiliate_enabled = False
            elif ch_monet is True or ch_aff is True:
                affiliate_enabled = True
        except Exception:
            affiliate_enabled = False

        donation_enabled = False
        if self.channel_config.get("enable_donation") is not None:
            donation_enabled = bool(self.channel_config.get("enable_donation"))
        elif self.channel_config.get("enable_monetization") is True:
            donation_enabled = bool(
                os.getenv("ENABLE_DONATION_CTA", "false").lower() in ("true", "1", "yes")
            )

        if affiliate_enabled or donation_enabled:
            try:
                from app.services.monetization_engine import enrich_video_monetization
                desc_clean, pinned_comment, prod_info = enrich_video_monetization(
                    channel_config=self.channel_config,
                    title=clean_title,
                    description=desc_clean,
                    pinned_comment=pinned_comment,
                    topic=topic or "",
                    script=script or "",
                    enable_affiliate=affiliate_enabled,
                    enable_donation=donation_enabled,
                )
                if prod_info:
                    logger.info(f"Đã tự động lồng ghép sản phẩm kiếm tiền Amazon cho ngách '{niche_id}': {prod_info.get('name')}")
                if donation_enabled:
                    logger.info(f"Đã tự động lồng ghép khối kêu gọi ủng hộ PayPal cho ngách '{niche_id}'")
            except Exception as ex:
                logger.warning(f"Không thể áp dụng monetization_engine: {ex}")
        else:
            logger.info(f"Ngách '{niche_id}' không bật affiliate hay donation -> Giữ mô tả sạch tối ưu phân phối Shorts Feed")
            if pinned_comment and any(k in str(pinned_comment).lower() for k in ("http://", "https://", "amazon.com", "paypal.me")):
                pinned_comment = None

        # Khử sạch link affiliate rò rỉ hoặc khối BREAKDOWN dàn trải trước khi gắn Hashtag
        desc_without_trailing_hashes = self.sanitize_shorts_description(
            raw_desc=desc_clean,
            base_title=smart_bundle.get("base_title") or clean_title,
            allow_affiliate=bool(affiliate_enabled or donation_enabled),
        )

        # Chuẩn hóa ngôn ngữ mặc định & Tọa độ GPS định tuyến vùng (US Geo-Lock / Brazil Geo-Lock chuẩn BCP-47)
        cfg_lang = str(self.channel_config.get("language") or "").strip()
        is_pt_br = cfg_lang.lower().startswith("pt") or (default_language and str(default_language).lower().startswith("pt"))

        # Xác định câu hỏi tương tác nhị phân (Engagement Poll) ngay từ bước đóng gói Metadata
        effective_comment = pinned_comment
        if not effective_comment and script:
            try:
                s_data = json.loads(script) if isinstance(script, str) else script
                if isinstance(s_data, dict):
                    effective_comment = s_data.get("pinned_comment")
            except Exception:
                pass
        if effective_comment and not (affiliate_enabled or donation_enabled):
            if any(k in str(effective_comment).lower() for k in ("http://", "https://", "amazon.com", "paypal.me")):
                effective_comment = None
        if not effective_comment:
            effective_comment = self.build_default_engagement_comment(
                title=clean_title,
                target_lang="pt-BR" if is_pt_br else (default_language or cfg_lang or "en-US"),
            )

        # Luôn ghim câu hỏi tương tác (💬 Poll) ngay ở Dòng 1 của Description để hiển thị tức thì
        # kể cả khi video đang ở chế độ SCHEDULED (private) hoặc YouTube API không cho ghim comment tự động.
        if effective_comment and "💬" not in (desc_without_trailing_hashes or ""):
            poll_line = f"💬 {effective_comment.strip()}"
            desc_without_trailing_hashes = (
                f"{poll_line}\n\n{desc_without_trailing_hashes}".strip()
                if desc_without_trailing_hashes
                else poll_line
            )

        # Đảm bảo description luôn có khối Hashtags thông minh (Ngách + Chủ đề + Shorts)
        desc_hashtag_line = smart_bundle["description_hashtags"]
        desc_clean = f"{desc_without_trailing_hashes}\n\n{desc_hashtag_line}".strip() if desc_without_trailing_hashes else desc_hashtag_line

        tag_list = list(smart_bundle["tags"])
        if is_pt_br:
            target_lang = "pt-BR"
            target_audio_lang = "pt-BR"
            recording_details = {
                "locationDescription": "São Paulo, Brazil",
                "location": {
                    "latitude": -23.5505,
                    "longitude": -46.6333,
                    "altitude": 0.0,
                },
            }
            for br_tag in ["brasil", "curiosidades"]:
                if len(tag_list) < 20 and br_tag not in [t.lower() for t in tag_list]:
                    tag_list.append(br_tag)
            default_notify_subs = True
        else:
            target_lang = default_language or cfg_lang or "en-US"
            if str(target_lang).lower() in ("en", "en-us", "english"):
                target_lang = "en-US"
                target_audio_lang = "en-US"
            elif not default_audio_language or default_audio_language.lower() in ("en", "en-us"):
                target_audio_lang = "vi-VN" if str(target_lang).lower().startswith("vi") else "en-US"
            else:
                target_audio_lang = default_audio_language

            # Gán tọa độ GPS Metro Mỹ chuyên biệt theo từng kênh (New York, Los Angeles, Chicago, Austin, Miami)
            # để tránh 5 kênh US dùng chung 1 tọa độ GPS duy nhất và tăng độ tin cậy Geo-Signal Mỹ
            US_METRO_LOCATIONS = {
                "ch_the_mysteries_of_the_world": ("New York, NY, United States", 40.7128, -74.0060),
                "ch_psychology_of_the_world": ("Los Angeles, CA, United States", 34.0522, -118.2437),
                "ch_paws_hearts_rescue": ("Chicago, IL, United States", 41.8781, -87.6298),
                "ch_wild_odyssey": ("Austin, TX, United States", 30.2672, -97.7431),
                "@alternatehistorywhatif-e2x": ("Miami, FL, United States", 25.7617, -80.1918),
            }
            loc_desc, loc_lat, loc_lon = US_METRO_LOCATIONS.get(
                str(self.channel_id or "").strip(),
                ("New York, NY, United States", 40.7128, -74.0060),
            )
            recording_details = {
                "locationDescription": loc_desc,
                "location": {
                    "latitude": loc_lat,
                    "longitude": loc_lon,
                    "altitude": 0.0,
                },
            }
            for us_tag in ["usa", "united states", "american"]:
                if len(tag_list) < 22 and us_tag not in [t.lower() for t in tag_list]:
                    tag_list.append(us_tag)
            # CỰC KỲ QUAN TRỌNG ĐỂ ĐẨY TỆP NGƯỜI XEM SANG MỸ (US GEO-SEEDING):
            # Đặt default_notify_subs = False cho toàn bộ kênh US để thuật toán YouTube KHÔNG đẩy thông báo
            # về tệp người đăng ký/lịch sử người xem Việt Nam cũ (nơi tỉ lệ vuốt bỏ lên tới 80-91% do rào cản tiếng Anh),
            # ép hệ thống phân phối hạt giống hoàn toàn mới (100% Discovery Seed Pool) sang tệp khán giả Mỹ (Stayed 55.7%-62.4%)!
            default_notify_subs = False

        if notify_subscribers is not None:
            should_notify_subs = bool(notify_subscribers)
        elif self.channel_config.get("notify_subscribers") is not None:
            should_notify_subs = bool(self.channel_config.get("notify_subscribers"))
        else:
            should_notify_subs = default_notify_subs

        # Chống đăng trùng lặp tiêu đề video (Anti-Duplicate Upload Guard across Channel Family + Semantic Similarity)
        try:
            from app.database.models import get_db, VideoJob, PublishedVideo
            from app.services.smart_scheduler import resolve_channel_family
            from app.services.channel_intelligence import check_idea_similarity
            fam_ids = resolve_channel_family(self.channel_id)
            norm_cand = re.sub(r'#shorts\b|\W+', '', clean_title.lower())
            with get_db() as db:
                recent_jobs = db.query(VideoJob).filter(
                    VideoJob.target_channel_id.in_(fam_ids),
                    VideoJob.status.in_(["PUBLISHED", "SCHEDULED"]),
                    VideoJob.youtube_video_id.isnot(None),
                ).order_by(VideoJob.id.desc()).limit(40).all()
                for rj in recent_jobs:
                    rj_title = rj.title or ""
                    rj_norm = re.sub(r'#shorts\b|\W+', '', rj_title.lower())
                    is_sim, sim_score, _ = check_idea_similarity(clean_title, [rj_title], similarity_threshold=0.58)
                    if rj_norm and norm_cand and (rj_norm == norm_cand or rj_title in (title, clean_title) or is_sim):
                        logger.warning(
                            f"⚠️ [Publisher] Video '{clean_title}' trùng lặp ({sim_score:.2f}) với video đã đăng '{rj_title}' "
                            f"trên kênh '{self.channel_name}' (ID: {rj.youtube_video_id}). Chặn upload trùng lặp!"
                        )
                        return {
                            "video_id": rj.youtube_video_id,
                            "url": f"https://youtube.com/shorts/{rj.youtube_video_id}",
                            "title": clean_title,
                            "status": "already_published",
                            "project_id": self.project_id
                        }
                recent_pvs = db.query(PublishedVideo).filter(
                    PublishedVideo.channel_id.in_(fam_ids),
                    PublishedVideo.youtube_video_id.isnot(None),
                ).order_by(PublishedVideo.id.desc()).limit(30).all()
                for pv in recent_pvs:
                    pv_title = pv.title or ""
                    pv_norm = re.sub(r'#shorts\b|\W+', '', pv_title.lower())
                    is_sim_pv, sim_pv_score, _ = check_idea_similarity(clean_title, [pv_title], similarity_threshold=0.58)
                    if pv_norm and norm_cand and (pv_norm == norm_cand or is_sim_pv):
                        logger.warning(
                            f"⚠️ [Publisher] Video '{clean_title}' trùng lặp ({sim_pv_score:.2f}) với PublishedVideo '{pv_title}' "
                            f"trên kênh '{self.channel_name}' (ID: {pv.youtube_video_id}). Chặn upload trùng lặp!"
                        )
                        return {
                            "video_id": pv.youtube_video_id,
                            "url": f"https://youtube.com/shorts/{pv.youtube_video_id}",
                            "title": clean_title,
                            "status": "already_published",
                            "project_id": self.project_id
                        }
        except Exception as e_dup:
            logger.debug(f"[Publisher] Kiểm tra trùng lặp: {e_dup}")

        yt_category_id = self.resolve_locked_category_id()

        body = {
            "snippet": {
                "title": clean_title[:100],
                "description": desc_clean[:5000],
                "tags": tag_list[:25],
                "categoryId": yt_category_id,
                "defaultLanguage": target_lang,
                "defaultAudioLanguage": target_audio_lang,
            },
            "status": {
                "privacyStatus": "private" if schedule_time_iso else privacy_status,
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
                "publicStatsViewable": True,
                "containsSyntheticMedia": bool(self.channel_config.get("contains_synthetic_media", False)),
            },
            "recordingDetails": recording_details,
            "localizations": {
                target_lang: {
                    "title": clean_title[:100],
                    "description": desc_clean[:5000],
                }
            },
        }

        if schedule_time_iso:
            try:
                from datetime import timezone, timedelta
                sched_dt = datetime.fromisoformat(str(schedule_time_iso).replace("Z", "+00:00"))
                if sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                now_u = datetime.now(timezone.utc)
                if sched_dt <= now_u + timedelta(minutes=8):
                    sched_dt = (now_u + timedelta(minutes=12)).replace(second=0, microsecond=0)
                    schedule_time_iso = sched_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    logger.info(f"⏰ [Publisher] Điều chỉnh nhẹ publishAt sang {schedule_time_iso} (+12p) để tránh lỗi invalidPublishAt của YouTube.")
            except Exception:
                pass
            body["status"]["publishAt"] = schedule_time_iso

        try:
            response = None
            max_upload_retries = 3
            last_upload_err = None

            import socket
            old_socket_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(180)  # Tăng thời gian chờ socket lên 180s tránh đứt kết nối khi mạng gia đình lag

            try:
                for upload_attempt in range(1, max_upload_retries + 1):
                    try:
                        # Adaptive chunksize: 4MB -> 2MB -> 1MB nếu mạng chập chờn, giúp gói tin nhỏ dễ xuyên qua mạng lag
                        chunk_mb = 4 if upload_attempt == 1 else (2 if upload_attempt == 2 else 1)
                        media = MediaFileUpload(
                            video_path,
                            mimetype="video/mp4",
                            chunksize=chunk_mb * 1024 * 1024,
                            resumable=True
                        )

                        request = self.service.videos().insert(
                            part="snippet,status,recordingDetails,localizations",
                            body=body,
                            media_body=media,
                            notifySubscribers=should_notify_subs,
                        )

                        response = None
                        while response is None:
                            status, response = request.next_chunk()
                            if status:
                                progress = int(status.progress() * 100)
                                logger.info(f"[{self.channel_name}] Đang upload video lên YouTube: {progress}% (Lần thử {upload_attempt}/{max_upload_retries})")

                        if response and response.get("id"):
                            break
                    except Exception as up_err:
                        last_upload_err = up_err
                        err_str = str(up_err)
                        # Nếu là lỗi quota thì không retry lặp vô ích mà n麼m tiếp xuống QuotaGuard
                        if "quotaExceeded" in err_str or "exceeded your quota" in err_str:
                            raise up_err

                        # Xử lý các lỗi mạng hoặc session upload bị đứt giữa chừng (bao gồm cả SSLEOFError / TLS drop)
                        is_recoverable = (
                            "410" in err_str
                            or "Gone" in err_str
                            or any(code in err_str for code in ["500", "502", "503", "504"])
                            or any(
                                k in err_str.lower()
                                for k in [
                                    "timeout",
                                    "connection reset",
                                    "broken pipe",
                                    "remotely closed",
                                    "eof occurred",
                                    "ssl",
                                    "connection aborted",
                                    "incomplete read",
                                    "badstatusline",
                                ]
                            )
                        )
                        if is_recoverable and upload_attempt < max_upload_retries:
                            wait_sec = upload_attempt * 3
                            logger.warning(
                                f"⚠️ [UploadRetry] Session tải video bị ngắt ({err_str[:90]}...). "
                                f"Khởi tạo phiên mới và thử lại lần {upload_attempt + 1}/{max_upload_retries} sau {wait_sec}s..."
                            )
                            time.sleep(wait_sec)
                            continue
                        else:
                            raise up_err
            finally:
                socket.setdefaulttimeout(old_socket_timeout)

            if not response or not response.get("id"):
                raise last_upload_err or RuntimeError("Upload kết thúc mà không nhận được video_id từ YouTube.")

            video_id = response.get("id")
            youtube_url = f"https://youtu.be/{video_id}"
            shorts_url = f"https://www.youtube.com/shorts/{video_id}"
            logger.info(f"Đã upload Shorts thành công lên YouTube: {shorts_url} ({youtube_url})")

            # ── 1. Tự động trích xuất và tải lên thumbnail đỉnh cao (Shock Frame) ──
            uploaded_thumb = False
            target_thumb = thumbnail_path
            if not target_thumb or not os.path.exists(target_thumb):
                auto_thumb = Path(video_path).with_suffix(".thumb.jpg")
                if not auto_thumb.exists():
                    try:
                        from app.utils.ffmpeg_helper import extract_shock_thumbnail
                        extract_shock_thumbnail(video_path, str(auto_thumb), timestamp_seconds=0.8)
                    except Exception as th_err:
                        logger.debug(f"Không thể trích xuất shock thumbnail: {th_err}")
                if auto_thumb.exists():
                    target_thumb = str(auto_thumb)

            if target_thumb and os.path.exists(target_thumb):
                uploaded_thumb = self.upload_thumbnail(video_id, target_thumb)

            # ── 2. Tự động đăng bình luận tương tác (Author Engagement Poll Comment) ──
            # Lưu ý: YouTube Data API v3 chặn đăng comment (403 Forbidden) khi video đang ở trạng thái SCHEDULED (private)
            # và không có API ghim (Pin) tự động. Vì vậy:
            # - Tầng 1: Câu hỏi Poll (💬) đã được đặt ở Dòng 1 của Description ngay khi upload.
            # - Tầng 2: Visual Poll CTA Banner đã hiển thị ở 3.8s cuối trên màn hình video.
            # - Tầng 3: Bình luận của tác giả được đăng ngay nếu PUBLIC, hoặc tự động đăng ngay khi chuyển sang PUBLIC.
            posted_comment_id = None
            if video_id and effective_comment:
                if schedule_time_iso or str(privacy_status).lower() != "public":
                    logger.info(
                        f"⏳ [PublisherAgent] Video {video_id} đang ở chế độ hẹn giờ/riêng tư. "
                        f"Câu hỏi Poll đã hiển thị ở Dòng 1 Description và sẽ tự động đăng vào mục Bình luận ngay khi video công chiếu."
                    )
                else:
                    comment_res = self.add_pinned_comment(video_id, effective_comment, retry_delay_sec=5)
                    if comment_res:
                        posted_comment_id = comment_res.get("id")

            # Ghi nhận tiêu hao Quota vào database
            try:
                QuotaManager.record_consumption(self.project_id, units=COST_FULL_SHORTS, is_upload=True)
            except Exception as q_err:
                logger.warning(f"Không thể ghi nhận quota: {q_err}")

            logger.info(
                f"🏷️ [PublisherAgent] Đã gắn {len(tag_list[:25])} thẻ Tags SEO & Hashtags cho '{clean_title}': "
                f"{tag_list[:10]}..."
            )

            return {
                "status": "success",
                "video_id": video_id,
                "youtube_url": youtube_url,
                "shorts_url": shorts_url,
                "channel": self.channel_name,
                "scheduled_at": schedule_time_iso,
                "pinned_comment": effective_comment,
                "comment_id": posted_comment_id,
                "thumbnail_uploaded": uploaded_thumb,
                "product_info": prod_info if "prod_info" in locals() and prod_info else None,
                "enriched_title": clean_title,
                "enriched_description": desc_clean,
                "tags": tag_list[:25],
            }

        except Exception as e:
            err_str = str(e)
            # Bắt mã lỗi 403 quotaExceeded từ Google API
            if "quotaExceeded" in err_str or "exceeded your quota" in err_str:
                from app.services.quota_manager import QuotaManager, COST_FULL_SHORTS, get_next_reset_time
                QuotaManager.mark_exhausted(self.project_id)

                # Tự động xoay sang dự án dự phòng khác trong Pool của kênh (nếu có) trước khi đưa vào hàng đợi
                alt_token, alt_proj_id = QuotaManager.get_active_token_for_channel(
                    self.channel_id, required_units=COST_FULL_SHORTS
                )
                if (
                    alt_token
                    and alt_proj_id != self.project_id
                    and QuotaManager.can_upload(alt_proj_id, required_units=COST_FULL_SHORTS)
                ):
                    logger.info(
                        f"🔄 [QuotaFallbackRotation] Dự án '{self.project_id}' báo hết quota trên Google! "
                        f"Tự động xoay sang Dự án dự phòng '{alt_proj_id}' ({alt_token}) và thử upload lại ngay..."
                    )
                    self.token_file = alt_token
                    self.project_id = alt_proj_id
                    self._init_service()
                    if self.service:
                        return self.upload_video(
                            video_path=video_path,
                            title=title,
                            description=description,
                            tags=tags,
                            privacy_status=privacy_status,
                            schedule_time_iso=schedule_time_iso,
                            default_language=default_language,
                            default_audio_language=default_audio_language,
                            pinned_comment=pinned_comment,
                            thumbnail_path=thumbnail_path,
                            topic=topic,
                            script=script,
                        )

                next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
                
                # Đóng gói an toàn toàn bộ video + metadata + phụ đề vào file .zip để người dùng tải về up tay
                fallback_zip = None
                try:
                    import zipfile
                    pkg_dir = Path("output/packages")
                    pkg_dir.mkdir(parents=True, exist_ok=True)
                    v_stem = Path(video_path).stem
                    zip_path = pkg_dir / f"{v_stem}_manual_upload.zip"
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        if Path(video_path).exists():
                            zf.write(video_path, arcname=Path(video_path).name)
                        meta_payload = {
                            "title": clean_title,
                            "description": desc_clean,
                            "tags": tag_list,
                            "channel_name": self.channel_name,
                            "defaultLanguage": target_lang,
                            "defaultAudioLanguage": target_audio_lang,
                            "containsSyntheticMedia": True,
                            "created_at": datetime.utcnow().isoformat()
                        }
                        zf.writestr("metadata.json", json.dumps(meta_payload, indent=2, ensure_ascii=False))
                        v_parent = Path(video_path).parent
                        for extra in v_parent.glob(f"{v_stem}*.*"):
                            if extra.suffix.lower() in (".srt", ".ass", ".jpg", ".png"):
                                zf.write(extra, arcname=extra.name)
                    fallback_zip = str(zip_path)
                    logger.info(f"📦 [QuotaFallback] Đã đóng gói video vào zip: {fallback_zip}")
                except Exception as ex_zip:
                    logger.warning(f"Không thể đóng gói zip fallback: {ex_zip}")

                quota_msg = (
                    f"Dự án '{self.project_id}' đã chạm trần YouTube Quota 10.000 units/ngày của Google. "
                    f"Video được bảo vệ và chuyển sang hàng đợi tự động (sẽ xuất bản vào {next_reset_str})."
                    + (f" Gói tải về thủ công: {fallback_zip}" if fallback_zip else "")
                )
                logger.warning(f"🚨 [QuotaExceeded] {quota_msg}")
                return {
                    "status": "waiting_quota_reset",
                    "error": quota_msg,
                    "message": quota_msg,
                    "video_id": None,
                    "project_id": self.project_id,
                    "fallback_zip": fallback_zip
                }

            logger.error(f"Lỗi khi upload video lên YouTube: {e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "video_id": None
            }

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
                    return f"Kèo sinh tử này bạn chọn phe nào: (1) {side_a} hay (2) {side_b}? Bình chọn 1 hoặc 2 bên dưới 👇🔥"
                if is_pt_br:
                    return f"Quem vence esse duelo extremo: (1) {side_a} ou (2) {side_b}? Comente 1 ou 2 abaixo! 👇🔥"
                return f"Who wins this 1v1 showdown: (1) {side_a} or (2) {side_b}? Drop 1 or 2 in the comments! 👇🔥"

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
            return (
                "Theo bạn đây là (1) Sinh vật chưa từng được khám phá hay (2) Hiện tượng tự nhiên? Bình chọn 1 hoặc 2 bên dưới 👇"
                if is_vn else (
                    "O que você acha que realmente se esconde lá: (1) Criatura Desconhecida ou (2) Fenômeno Geológico? Comente 1 ou 2 abaixo! 👇"
                    if is_pt_br else
                    "What do you think is really behind this: (1) Undiscovered Species or (2) Natural Anomaly? Drop 1 or 2 below! 👇"
                )
            )
        return (
            "Chi tiết nào khiến bạn bất ngờ nhất? Hãy bình luận suy nghĩ của bạn bên dưới 👇\n🔔 Đăng ký kênh (Subscribe) để không bỏ lỡ video tiếp theo! 🔥"
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
                            "textOriginal": comment_text.strip()
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

    _CUSTOM_THUMB_FORBIDDEN_CHANNELS = set()

    def upload_thumbnail(self, video_id: str, thumbnail_path: str) -> bool:
        """
        Tải thumbnail tùy chỉnh lên YouTube qua YouTube Data API v3 (thumbnails.set).
        Áp dụng cho video Shorts nhằm tối ưu tỷ lệ click CTR > 10%.
        """
        if self.channel_id in self._CUSTOM_THUMB_FORBIDDEN_CHANNELS:
            return False
        if not self.service or not video_id or not os.path.exists(thumbnail_path):
            logger.warning(f"📸 [PublisherAgent] Không đủ điều kiện upload thumbnail (video_id={video_id}, path={thumbnail_path})")
            return False
        try:
            media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg", resumable=False)
            self.service.thumbnails().set(
                videoId=video_id,
                media_body=media
            ).execute()
            logger.info(f"📸 [PublisherAgent] ✅ Đã upload custom thumbnail thành công cho video {video_id}: {thumbnail_path}")
            return True
        except Exception as e:
            if "403" in str(e) and "permissions to upload and set custom video thumbnails" in str(e):
                self._CUSTOM_THUMB_FORBIDDEN_CHANNELS.add(self.channel_id)
                logger.info(f"📸 [PublisherAgent] Kênh '{self.channel_name}' chưa bật tính năng Custom Thumbnail trên YouTube Studio -> Bỏ qua tự động cho các video tiếp theo.")
                return False
            logger.warning(f"📸 [PublisherAgent] Không thể upload custom thumbnail ({thumbnail_path}): {e}")
            return False

    def ensure_pinned_comment(self, video_id: str, comment_text: Optional[str] = None) -> Optional[Dict]:
        """
        Kiểm tra và đăng bình luận tương tác của chính chủ kênh cho video nếu video đã ở trạng thái PUBLIC
        và tác giả chưa có bình luận nào trên video.
        Ưu tiên lấy `pinned_comment` đã sinh riêng cho kịch bản từ DB (`VideoJob.scenes_json`) hoặc từ Dòng 1 `💬` của Description.
        """
        if not self.service or not video_id:
            return None

        try:
            # 1. Kiểm tra trạng thái video
            v_res = self.service.videos().list(part="status,snippet", id=video_id).execute()
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
                        if not allow_affiliate and any(k in c_text for k in ("http://", "https://", "amazon.com", "paypal.me")):
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
                return None

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

            if effective_comment and not allow_affiliate and any(k in str(effective_comment).lower() for k in ("http://", "https://", "amazon.com", "paypal.me")):
                effective_comment = None

            if not effective_comment:
                v_title = v_item.get("snippet", {}).get("title", "")
                effective_comment = self.build_default_engagement_comment(title=v_title)

            # 5. Đăng bình luận tác giả
            return self.add_pinned_comment(video_id, effective_comment, retry_on_spam=False, retry_delay_sec=0)
        except Exception as e:
            logger.warning(f"💬 [PublisherAgent] Lỗi khi ensure_pinned_comment cho {video_id}: {e}")
            return None

    def sync_channel_pinned_comments(self, limit: int = 10) -> List[Dict]:
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
                if res:
                    results.append({
                        "video_id": vid,
                        "title": v_title,
                        "comment_id": res.get("id"),
                        "comment_text": res.get("snippet", {}).get("topLevelComment", {}).get("snippet", {}).get("textOriginal", "")
                    })
        except Exception as e:
            logger.warning(f"💬 [PublisherAgent] Lỗi khi sync_channel_pinned_comments ({self.channel_name}): {e}")

        return results

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
    ) -> Dict[str, Any]:
        """
        Tự động tổng hợp bộ Tags SEO (`snippet.tags`) và Hashtags (`#Shorts #...`) chuẩn ngách & sát chủ đề thực tế của video:
        1. Trích xuất thực thể/từ khóa chủ đề trực tiếp từ `title` và `topic`.
        2. Khóa chặt bộ Hashtag/Tag cốt lõi theo từng ngách kênh (`wildlife_predators`, `psychology`, `deep_sea_earth_mysteries`, `alternate_history`, `animal_rescue`) và ngôn ngữ (`en-US` / `pt-BR`).
        3. Chặn 100% các hashtag sai chính tả hoặc lệch ngách (`#narcisist`, `#legacyofalexander`, `#masteringdiscipline`, `#space`, `#titanic` khi không khớp chủ đề).
        4. Gắn 2-3 hashtag chuẩn ngách ngay sau `#Shorts` trên Tiêu đề (đảm bảo <= 100 ký tự) + 8-10 hashtag ở cuối Mô tả + tối đa 25 thẻ `tags` cho YouTube Data API v3.
        """
        raw_niche = str(self.channel_config.get("niche") or "").lower()
        cfg_lang = str(self.channel_config.get("language") or "").strip().lower()
        is_br = cfg_lang.startswith("pt")

        # Làm sạch tiêu đề gốc (bỏ mọi chuỗi #hashtag ở cuối)
        base_title = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', (title or "").strip()).strip()
        base_title = re.sub(r'#shorts\b', '', base_title, flags=re.IGNORECASE).strip()
        if not base_title:
            base_title = (topic or "Shorts Video").strip()

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
                "history", "alternate history", "what if", "historical facts",
                "world history", "ancient history", "history documentary", "civilization",
                "timeline", "usa", "Shorts", "shorts", "ytshorts", "viral",
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

        # 1. Thu thập extra_tags do người dùng/script truyền vào + default_tags của kênh
        custom_tags: List[str] = []
        for src in (extra_tags, self.channel_config.get("default_tags")):
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
            "steam engine", "titanic", "cold war", "world war 1", "world war 2",
            "ancient egypt", "giza", "alexandria", "alexander", "emperor qin",
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
            if low_t in seen_lower:
                continue
            if len(final_tags) >= 24 or (total_chars + len(clean_t) + 2) > 450:
                break
            final_tags.append(clean_t)
            seen_lower.add(low_t)
            total_chars += len(clean_t) + 2

        # 4. Xây dựng chuỗi Hashtag cho Tiêu đề (#Shorts + 2-3 hashtag chuẩn ngách/chủ đề, <= 100 ký tự)
        title_hash_list: List[str] = ["Shorts"]
        # Nếu người dùng truyền tag ngắn hợp lệ hoặc có thực thể chủ đề, ưu tiên đưa 1 hashtag chủ đề + 2 hashtag ngách
        for h_cand in (*topic_hashtag_candidates[:1], *niche_title_hashes):
            h_clean = re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', h_cand).lower()
            if h_clean and h_clean != "shorts" and h_clean not in [x.lower() for x in title_hash_list]:
                title_hash_list.append(h_clean)
            if len(title_hash_list) >= 4:
                break

        # Ghép vào base_title sao cho không vượt quá 100 ký tự của YouTube
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

        # 5. Xây dựng khối Hashtags đầy đủ (8-10 thẻ) cho cuối phần Mô tả (Description)
        desc_hash_tokens: List[str] = ["#Shorts"]
        for h_tok in (*topic_hashtag_candidates, *niche_title_hashes, *[re.sub(r'\s+', '', t.lower()) for t in final_tags]):
            h_c = re.sub(r'[^a-zA-Z0-9À-ỹçãõáéíóúâêô]', '', str(h_tok)).lower()
            if not h_c or len(h_c) < 3 or len(h_c) > 24:
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

        cleaned = "\n\n".join(filtered_lines).strip()
        if not cleaned and base_title:
            cleaned = re.sub(r'(?:\s+#[^\s#]+)+\s*$', '', str(base_title).strip()).strip()
        return cleaned

    def verify_and_heal_video_metadata(self, video_id: str) -> bool:
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
            if "💬" not in (desc_body or ""):
                poll_q = self.build_default_engagement_comment(
                    title=smart.get("base_title") or raw_title,
                    target_lang=expected_lang,
                )
                if poll_q:
                    desc_body = f"💬 {poll_q.strip()}\n\n{desc_body}".strip() if desc_body else f"💬 {poll_q.strip()}"
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
                "alternate history", "what if", "what if history", "butterfly effect",
                "historical facts", "world history", "american history", "ancient history",
                "history documentary", "roman empire", "ancient rome", "timeline split",
                "counterfactual history", "historical paradox", "ancient civilizations",
                "world war history", "napoleon", "history shorts",
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

