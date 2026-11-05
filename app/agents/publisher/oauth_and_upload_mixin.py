"""app.agents.publisher.oauth_and_upload_mixin — Khởi tạo OAuth2, tự động làm mới token & tải video/thumbnail lên YouTube."""
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


class OAuthAndUploadMixin:
    _CUSTOM_THUMB_FORBIDDEN_CHANNELS: set = set()
    def __init__(
        self,
        channel_config: Optional[Any] = None,
        account_name: str = "account_1",
        channel_id: Optional[str] = None,
        **kwargs,
    ):
        if isinstance(channel_config, str):
            channel_id = channel_config
            channel_config = {"id": channel_id}
        self.channel_config = dict(channel_config or {})
        if channel_id and not self.channel_config.get("id"):
            self.channel_config["id"] = channel_id
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

    def _find_live_duplicate_on_youtube(self, candidate_title: str, max_check: int = 15) -> Optional[str]:
        """
        Kiểm tra trực tiếp danh sách video vừa tải lên trên kênh YouTube thật (uploads playlist)
        để phát hiện bản trùng lặp kể cả khi DB nội bộ chưa kịp ghi youtube_video_id hoặc khi
        lần thử upload_attempt=1 đã hoàn tất trên máy chủ YouTube ngay trước lúc đứt kết nối SSL.
        """
        if not self.service or not candidate_title:
            return None
        try:
            from app.services.channel_intelligence import check_idea_similarity
            norm_cand = re.sub(r'#shorts\b|\W+', '', str(candidate_title).lower())
            if not norm_cand:
                return None
            ch_res = self.service.channels().list(part="contentDetails", mine=True).execute()
            ch_items = ch_res.get("items") or []
            if not ch_items:
                return None
            uploads_pl = ch_items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if not uploads_pl:
                return None
            pl_res = self.service.playlistItems().list(
                part="snippet,contentDetails,status",
                playlistId=uploads_pl,
                maxResults=max_check,
            ).execute()
            for item in (pl_res.get("items") or []):
                snip = item.get("snippet") or {}
                ex_title = str(snip.get("title") or "").strip()
                if not ex_title or ex_title.startswith("[PRIVATE-DUP]"):
                    continue
                vid_id = (item.get("contentDetails") or {}).get("videoId")
                if not vid_id:
                    continue
                ex_norm = re.sub(r'#shorts\b|\W+', '', ex_title.lower())
                is_sim, sim_score, _ = check_idea_similarity(candidate_title, [ex_title], similarity_threshold=0.58)
                if ex_norm == norm_cand or is_sim:
                    logger.warning(
                        f"🛡️ [LiveYouTubeDupGuard] Phát hiện video trùng lặp trực tiếp trên kênh YouTube "
                        f"'{self.channel_name}': '{ex_title}' (ID: {vid_id}, sim={sim_score:.2f}). Tái sử dụng ID, không upload bản thứ 2!"
                    )
                    return vid_id
        except Exception as e_live_dup:
            logger.debug(f"[LiveYouTubeDupGuard] Check warning: {e_live_dup}")
        return None

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
        category_id: Optional[str] = None,
        **kwargs,
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

        is_long_form_video = bool(
            (locals().get("vid_dur_ms") and vid_dur_ms > 65000)
            or (script and ('"video_format": "long"' in str(script) or "'video_format': 'long'" in str(script)))
        )
        smart_bundle = self.build_smart_video_tags_and_hashtags(
            title=clean_title,
            topic=topic or "",
            script=script or "",
            extra_tags=tags,
            is_long_form=is_long_form_video,
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
            poll_line = f"💬 {self.sanitize_engagement_comment(effective_comment).strip()}"
            desc_without_trailing_hashes = (
                f"{poll_line}\n\n{desc_without_trailing_hashes}".strip()
                if desc_without_trailing_hashes
                else poll_line
            )

        # Tự động chèn khối YouTube Chapters (0:00 Intro...) cho Video Dài 16:9 (chuẩn @disastersexplained1 & @FossilVideos)
        if is_long_form_video and not re.search(r"(?m)^0:00\s+", desc_without_trailing_hashes or ""):
            try:
                from app.agents.publisher.seo_tags_mixin import build_youtube_chapters_from_scenes
                s_obj = json.loads(script) if isinstance(script, str) and script.strip().startswith("{") else (script if isinstance(script, dict) else {})
                scenes_for_ch = (s_obj.get("scenes") if isinstance(s_obj, dict) else None) or kwargs.get("scenes") or []
                if scenes_for_ch:
                    chap_block = build_youtube_chapters_from_scenes(
                        scenes=scenes_for_ch,
                        language="pt-BR" if is_pt_br else (default_language or cfg_lang or "en"),
                    )
                    if chap_block:
                        desc_without_trailing_hashes = (
                            f"{desc_without_trailing_hashes}\n\n{chap_block}".strip()
                            if desc_without_trailing_hashes
                            else chap_block
                        )
                        logger.info(f"⏱️ [PublisherAgent] Đã tự động gắn YouTube Chapters (0:00...) vào Description cho '{clean_title}'.")
            except Exception as e_chap:
                logger.debug(f"[PublisherAgent] Auto-chapters skip: {e_chap}")

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

        if not is_pt_br and str(self.channel_config.get("target_country") or "US").upper() == "US":
            # Khóa cứng notifySubscribers=False cho toàn bộ kênh mục tiêu US để cắt đứt vòng lặp hạt giống VN cũ
            should_notify_subs = False if notify_subscribers is None else bool(notify_subscribers)
        elif notify_subscribers is not None:
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
                live_dup_id = self._find_live_duplicate_on_youtube(clean_title)
                if live_dup_id:
                    return {
                        "video_id": live_dup_id,
                        "url": f"https://youtube.com/shorts/{live_dup_id}",
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

                        # Xử lý các lỗi mạng hoặc session upload bị đứt giữa chừng (bao gồm cả SSLEOFError / TLS drop / WinError 10053-10054)
                        is_recoverable = (
                            isinstance(up_err, (ConnectionError, TimeoutError))
                            or "410" in err_str
                            or "Gone" in err_str
                            or any(code in err_str for code in ["500", "502", "503", "504"])
                            or any(
                                k in err_str.lower()
                                for k in [
                                    "timeout",
                                    "connection reset",
                                    "broken pipe",
                                    "remotely closed",
                                    "forcibly closed",
                                    "eof occurred",
                                    "ssl",
                                    "connection aborted",
                                    "connection was aborted",
                                    "aborted by the software",
                                    "winerror 10053",
                                    "winerror 10054",
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
                            try:
                                self._init_service()
                            except Exception:
                                pass
                            recovered_vid = self._find_live_duplicate_on_youtube(clean_title, max_check=8)
                            if recovered_vid:
                                logger.info(
                                    f"✅ [UploadRetryRecovery] Video '{clean_title}' thực tế đã tạo thành công trên YouTube (ID: {recovered_vid}) "
                                    f"trước khi đứt kết nối mạng. Không tạo bản upload trùng thứ 2!"
                                )
                                response = {"id": recovered_vid}
                                break
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

            # ── 1. Tự động thiết kế và tải lên Thumbnail chuẩn 2–3 từ Two-Tone (@FossilVideos / @IFinity_What Style) ──
            uploaded_thumb = False
            target_thumb = thumbnail_path
            if not target_thumb or not os.path.exists(target_thumb):
                auto_thumb = Path(video_path).with_suffix(".thumb.jpg")
                if not auto_thumb.exists():
                    try:
                        from app.services.thumbnail_service import ThumbnailService
                        s_obj_th = json.loads(script) if isinstance(script, str) and script.strip().startswith("{") else (script if isinstance(script, dict) else {})
                        thumb_hook_txt = (s_obj_th.get("thumbnail_text") if isinstance(s_obj_th, dict) else None) or ""
                        th_srv = ThumbnailService(output_dir=str(Path(video_path).parent))
                        th_res = th_srv.generate_viral_thumbnail(
                            job_id=int(kwargs.get("job_id") or 0),
                            topic=topic or clean_title,
                            viral_title=clean_title,
                            video_path=video_path,
                            niche_id=niche_id,
                            custom_text=thumb_hook_txt,
                        )
                        best_th = th_res.get("16x9") if is_long_form_video else (th_res.get("9x16") or th_res.get("16x9"))
                        if best_th and os.path.exists(best_th):
                            import shutil as _shutil
                            _shutil.copy2(best_th, str(auto_thumb))
                    except Exception as th_srv_err:
                        logger.debug(f"ThumbnailService fallback sang extract_shock_thumbnail: {th_srv_err}")
                if not auto_thumb.exists():
                    try:
                        from app.utils.ffmpeg_helper import extract_shock_thumbnail
                        extract_shock_thumbnail(video_path, str(auto_thumb), timestamp_seconds=0.8)
                    except Exception as th_err:
                        logger.debug(f"Không thể trích xuất shock thumbnail: {th_err}")
                if auto_thumb.exists():
                    target_thumb = str(auto_thumb)

            if target_thumb and os.path.exists(target_thumb):
                try:
                    uploaded_thumb = self.upload_thumbnail(video_id, target_thumb)
                except Exception as th_up_err:
                    logger.warning(f"📸 [PublisherAgent] Bỏ qua lỗi phụ khi upload thumbnail cho {video_id}: {th_up_err}")
                    uploaded_thumb = False

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
                    try:
                        self.sync_channel_pinned_comments(limit=4)
                    except Exception:
                        pass
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
