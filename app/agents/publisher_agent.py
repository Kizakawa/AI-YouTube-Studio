import os
import re
import json
import base64
import logging
from pathlib import Path
from typing import Optional, Dict, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

class PublisherAgent:
    """Agent chịu trách nhiệm xác thực OAuth2 và đăng tải/lên lịch video lên các kênh YouTube."""

    def __init__(self, channel_config: Optional[Dict] = None, account_name: str = "account_1"):
        self.channel_config = channel_config or {}
        self.channel_id = self.channel_config.get("id", "default")
        self.channel_name = self.channel_config.get("name", "YouTube Channel")
        self.account_name = self.channel_config.get("google_account", account_name)

        # Quản lý Hạn mức Quota theo Google Cloud Project ID
        from app.services.quota_manager import QuotaManager, get_project_id_from_client_secret, COST_FULL_SHORTS, get_next_reset_time
        cs_path = self.channel_config.get("client_secret") or f"credentials/accounts/{self.account_name}/client_secret.json"
        self.project_id = QuotaManager.get_channel_project_id(self.channel_id)
        
        ch_token = f"credentials/channels/{self.channel_id}/youtube_token.json"
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
        elif os.path.exists(ch_token):
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
                # Tự động refresh token nếu hết hạn
                if creds.expired and creds.refresh_token:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                    # Lưu lại token đã refresh được mã hoá AES-256
                    if os.path.exists(self.token_file):
                        from app.utils.security import save_encrypted_json
                        save_encrypted_json(self.token_file, json.loads(creds.to_json()))
                        logger.info(f"Đã tự động refresh & lưu mã hoá AES-256 YouTube token cho '{self.channel_name}'")

                self.service = build("youtube", "v3", credentials=creds)
                self.init_error = None
                logger.info(f"YouTube Data API v3 đã kết nối thành công cho '{self.channel_name}'")
            else:
                self.init_error = f"File token rỗng hoặc không giải mã được: {self.token_file}"
                logger.warning(f"Chưa tìm thấy youtube_token hợp lệ cho '{self.token_file}'")
        except Exception as e:
            self.init_error = str(e)
            logger.error(f"Lỗi khởi tạo YouTube API cho '{self.channel_name}': {e}")

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
    ) -> Dict:
        """
        Upload video lên YouTube qua YouTube Data API v3.
        Nếu có schedule_time_iso (ví dụ '2026-09-25T19:00:00Z'), video sẽ được đặt ở chế độ private và lên lịch công chiếu.
        Đã bổ sung defaultLanguage & defaultAudioLanguage để định tuyến thuật toán đề xuất chính xác vùng người xem.
        Tự động gắn link Amazon Affiliate chuẩn xác vào Mô tả & Bình luận mồi.
        """
        import re
        if not self.service:
            self._init_service()

        if not self.service:
            if self.init_error and ("invalid_grant" in self.init_error or "expired" in self.init_error):
                err_msg = (
                    f"⚠️ Phiên đăng nhập YouTube của kênh '{self.channel_name}' đã hết hạn (Google OAuth token expired). "
                    f"Vui lòng chạy lệnh 'python auth_channel.py {self.channel_id}' trong terminal hoặc vào Web Dashboard (http://127.0.0.1:8000) -> Tab Kênh & Tài Khoản -> Bấm '🔑 Cấp Quyền YouTube (Kênh Này)' để kết nối lại."
                )
            else:
                err_msg = (
                    f"Kênh '{self.channel_name}' chưa có token YouTube riêng hoặc chưa được cấp quyền ({self.init_error or 'Chưa xác thực'}). "
                    f"Vui lòng vào Web Dashboard -> Tab Kênh & Tài Khoản -> Bấm '🔑 Cấp Quyền YouTube (Kênh Này)' hoặc chạy 'python auth_channel.py {self.channel_id}'."
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
        # QUOTA GUARD: Kiểm tra hạn mức trước khi gọi API (Pre-flight check)
        # ──────────────────────────────────────────────────────────
        from app.services.quota_manager import QuotaManager, COST_FULL_SHORTS, get_next_reset_time
        if not QuotaManager.can_upload(self.project_id, required_units=COST_FULL_SHORTS):
            next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
            quota_msg = (
                f"Hạn mức YouTube API hôm nay của dự án '{self.project_id}' đã đạt giới hạn an toàn (gần chạm trần 10.000 units). "
                f"Video đã được tự động lưu và chuyển vào hàng đợi an toàn, sẽ tự động đăng sau khi Google cấp hạn mức mới lúc {next_reset_str}."
            )
            logger.warning(f"⚠️ [QuotaGuard] {quota_msg}")
            return {
                "status": "waiting_quota_reset",
                "error": quota_msg,
                "message": quota_msg,
                "video_id": None,
                "project_id": self.project_id
            }

        # Khử hậu tố kênh/quốc gia rò rỉ (ví dụ " - Maya Leader VN" hay " - VN")
        clean_title = re.sub(
            r'[\[\(\s\-_|:]+[\w\s]*?\b(?:VN|VI|Vietnamese|Vietnam)\b.*$',
            '',
            title,
            flags=re.IGNORECASE
        ).strip()
        if not clean_title:
            clean_title = title

        # Đảm bảo video được gắn cờ và phân loại 100% là YouTube Shorts
        if "#shorts" not in clean_title.lower():
            if len(clean_title) > 91:
                clean_title = clean_title[:91].rstrip()
            clean_title = f"{clean_title} #Shorts"

        # Tự động tối ưu hóa kiếm tiền qua Amazon Affiliate (Monetization Engine)
        prod_info = None
        try:
            from app.services.monetization_engine import enrich_video_monetization
            desc_clean, pinned_comment, prod_info = enrich_video_monetization(
                channel_config=self.channel_config,
                title=clean_title,
                description=description or "",
                pinned_comment=pinned_comment,
                topic=topic or "",
                script=script or ""
            )
            if prod_info:
                logger.info(f"Đã tự động lồng ghép sản phẩm kiếm tiền Amazon: {prod_info.get('name')}")
        except Exception as ex:
            logger.warning(f"Không thể áp dụng monetization_engine: {ex}")
            desc_clean = description or ""

        # Đảm bảo description luôn có hashtag nhận diện Shorts
        shorts_hashtags = "#Shorts #shorts #viral #ytshorts"
        if "#shorts" not in desc_clean.lower():
            desc_clean = f"{desc_clean.strip()}\n\n{shorts_hashtags}" if desc_clean.strip() else shorts_hashtags

        tag_list = []
        if isinstance(tags, list):
            tag_list = list(tags)
        elif isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        for req_tag in ["Shorts", "shorts", "ytshorts", "viral"]:
            if req_tag.lower() not in [t.lower() for t in tag_list]:
                tag_list.append(req_tag)

        body = {
            "snippet": {
                "title": clean_title[:100],
                "description": desc_clean[:5000],
                "tags": tag_list[:20],
                "categoryId": "22"  # People & Blogs / Shorts
            },
            "status": {
                "privacyStatus": "private" if schedule_time_iso else privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        if default_language:
            body["snippet"]["defaultLanguage"] = default_language
        if default_audio_language:
            body["snippet"]["defaultAudioLanguage"] = default_audio_language

        if schedule_time_iso:
            body["status"]["publishAt"] = schedule_time_iso

        try:
            media = MediaFileUpload(
                video_path,
                mimetype="video/mp4",
                chunksize=1024 * 1024,
                resumable=True
            )

            request = self.service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"Đang upload video lên YouTube: {progress}%")

            video_id = response.get("id")
            youtube_url = f"https://youtu.be/{video_id}"
            shorts_url = f"https://www.youtube.com/shorts/{video_id}"
            logger.info(f"Đã upload Shorts thành công lên YouTube: {shorts_url} ({youtube_url})")

            # Tự động đăng bình luận mồi nếu có
            posted_comment_id = None
            if video_id and pinned_comment:
                comment_res = self.add_pinned_comment(video_id, pinned_comment)
                if comment_res:
                    posted_comment_id = comment_res.get("id")

            # Ghi nhận tiêu hao Quota vào database
            try:
                QuotaManager.record_consumption(self.project_id, units=COST_FULL_SHORTS, is_upload=True)
            except Exception as q_err:
                logger.warning(f"Không thể ghi nhận quota: {q_err}")

            return {
                "status": "success",
                "video_id": video_id,
                "youtube_url": youtube_url,
                "shorts_url": shorts_url,
                "channel": self.channel_name,
                "scheduled_at": schedule_time_iso,
                "pinned_comment": pinned_comment,
                "comment_id": posted_comment_id,
                "product_info": prod_info if "prod_info" in locals() and prod_info else None,
                "enriched_description": desc_clean,
            }

        except Exception as e:
            err_str = str(e)
            # Bắt mã lỗi 403 quotaExceeded từ Google API
            if "quotaExceeded" in err_str or "exceeded your quota" in err_str:
                from app.services.quota_manager import QuotaManager, get_next_reset_time
                QuotaManager.mark_exhausted(self.project_id)
                next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
                quota_msg = (
                    f"Dự án '{self.project_id}' đã chạm trần YouTube Quota 10.000 units/ngày của Google. "
                    f"Video được bảo vệ và chuyển sang hàng đợi tự động, sẽ xuất bản vào {next_reset_str}."
                )
                logger.warning(f"🚨 [QuotaExceeded] {quota_msg}")
                return {
                    "status": "waiting_quota_reset",
                    "error": quota_msg,
                    "message": quota_msg,
                    "video_id": None,
                    "project_id": self.project_id
                }

            logger.error(f"Lỗi khi upload video lên YouTube: {e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "video_id": None
            }

    def add_pinned_comment(self, video_id: str, comment_text: str) -> Optional[Dict]:
        """
        Tự động đăng bình luận mồi (Engagement Bait Comment) của chính chủ kênh lên video mới upload.
        Bình luận của tác giả giúp kích thích khán giả comment trả lời và đẩy thời lượng xem trung bình (APV) lên >160%.
        """
        if not self.service or not video_id or not comment_text:
            return None
        try:
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
            res = self.service.commentThreads().insert(
                part="snippet",
                body=body
            ).execute()
            comment_id = res.get("id")
            logger.info(f"💬 [PublisherAgent] ✅ Đã tự động đăng bình luận mồi lên video {video_id}: '{comment_text[:40]}...' (Comment ID: {comment_id})")
            return res
        except Exception as e:
            logger.warning(f"💬 [PublisherAgent] Không thể tự động post bình luận mồi lên video {video_id}: {e}")
            return None
