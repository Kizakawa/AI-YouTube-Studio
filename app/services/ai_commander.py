"""
Universal AI Commander & Intelligent Intent Router (RAG-Assisted Agent)
Cho phép người dùng ra lệnh bằng ngôn ngữ tự nhiên qua Telegram (/chat hoặc tin nhắn tự nhiên).
Hệ thống tự động phân tích ngữ cảnh RAG (Kênh, Video trong DB, Hàng đợi, Cấu hình)
và điều phối công việc tới các Agent/Handler tương ứng.
Dễ dàng mở rộng thêm hàng loạt tính năng mới mà không cần thêm lệnh /slash.
"""
import os
import re
import json
import time
import asyncio
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Callable

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.database.models import get_db, User, VideoJob, log_activity
from app.services import channel_manager
from app.bot.keyboards import (
    get_delete_confirmation_keyboard,
    get_delete_failed_confirmation_keyboard,
    get_chat_brainstorm_keyboard,
)

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


def delete_video_job(job_id: int, user_id: int = 0, username: str = "Creator") -> Tuple[bool, str, Dict[str, Any]]:
    """
    Xóa VideoJob khỏi cơ sở dữ liệu và xóa sạch toàn bộ file liên quan trên ổ cứng (Video, Audio, Subtitle, Temp).
    Trả về: (thành công, thông báo, thông tin_video)
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return False, f"Không tìm thấy Video #{job_id} trong hệ thống.", {}

        info = {
            "id": job.id,
            "title": job.title or job.topic or f"Video #{job.id}",
            "channel_id": job.target_channel_id,
            "status": job.status,
            "video_path": job.video_path,
        }

        files_deleted = []

        # 1. Xóa file video chính
        if job.video_path and os.path.exists(job.video_path):
            try:
                os.remove(job.video_path)
                files_deleted.append(job.video_path)
            except Exception as e:
                logger.warning(f"Lỗi khi xóa file video {job.video_path}: {e}")

        # 2. Kiểm tra file output mặc định nếu khác video_path
        default_output = Path("storage/outputs") / f"video_{job.id}.mp4"
        if default_output.exists() and str(default_output) not in files_deleted:
            try:
                default_output.unlink()
                files_deleted.append(str(default_output))
            except Exception:
                pass

        # 3. Xóa audio và subtitle
        if job.audio_path and os.path.exists(job.audio_path):
            try:
                os.remove(job.audio_path)
                files_deleted.append(job.audio_path)
            except Exception:
                pass

        if job.subtitle_path and os.path.exists(job.subtitle_path):
            try:
                os.remove(job.subtitle_path)
                files_deleted.append(job.subtitle_path)
            except Exception:
                pass

        # 4. Xóa thư mục tạm thời của job
        job_temp_dir = Path("storage/temp") / f"job_{job.id}"
        if job_temp_dir.exists() and job_temp_dir.is_dir():
            try:
                shutil.rmtree(job_temp_dir, ignore_errors=True)
                files_deleted.append(str(job_temp_dir))
            except Exception:
                pass

        manual_temp_dir = Path("storage/temp") / f"manual_{job.id}"
        if manual_temp_dir.exists() and manual_temp_dir.is_dir():
            try:
                shutil.rmtree(manual_temp_dir, ignore_errors=True)
                files_deleted.append(str(manual_temp_dir))
            except Exception:
                pass

        # 5. Xóa khỏi database SQLite
        db.delete(job)
        log_activity(
            db,
            user_id,
            username,
            "DELETE_VIDEO",
            f"Đã xóa vĩnh viễn video #{job_id}: '{info['title']}' (giải phóng {len(files_deleted)} tệp)",
            channel_id=info["channel_id"]
        )

        info["deleted_files_count"] = len(files_deleted)
        return True, f"Đã xóa Video #{job_id} thành công!", info


class AICommander:
    """
    AI Quản lý Đa năng (Universal AI Commander):
    - Thu thập ngữ cảnh động (RAG) từ hệ sinh thái: Kênh, DB video, Hàng đợi, Web links.
    - Phân tích ý định người dùng (Intent Routing) bằng LLM hoặc Fast Heuristics.
    - Điều phối thực thi tác vụ với cơ chế An toàn (Yêu cầu xác nhận trước khi xóa).
    - Hỗ trợ Plugin / Tool Registry để mở rộng tính năng vô hạn trong tương lai.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self.tool_registry: Dict[str, Dict[str, Any]] = {}
        self._register_default_tools()

    def register_tool(self, name: str, description: str, parameters: Dict[str, str], handler: Callable):
        """Đăng ký một công cụ / tính năng mới vào hệ thống AI Manager."""
        self.tool_registry[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler
        }
        logger.info(f"Đã đăng ký công cụ AI Commander: {name}")

    def _register_default_tools(self):
        """Khởi tạo danh mục các công cụ hệ thống mặc định."""
        self.tool_registry = {
            "CREATE_VIDEO": {
                "description": "Tạo kịch bản video Shorts mới, lên ý tưởng nội dung theo chủ đề và kênh",
                "parameters": {"topic": "Chủ đề video", "director_notes": "Ghi chú phong cách/mood/niche (nếu có)", "channel_id": "Mã kênh mục tiêu (tùy chọn)"}
            },
            "RENDER_VIDEO": {
                "description": "Bắt đầu dựng/render video đã có kịch bản thành video MP4 hoàn chỉnh",
                "parameters": {"job_id": "Mã số Task/Video ID cần dựng"}
            },
            "REQUEST_DELETE_VIDEO": {
                "description": "Yêu cầu xóa video khỏi cơ sở dữ liệu và ổ cứng (BẮT BUỘC hiện bước xác nhận Yes/No với người dùng)",
                "parameters": {"job_id": "Mã số Video ID cần xóa (nếu có)", "query": "Tên hoặc mô tả video muốn xóa", "delete_all_failed": "true nếu muốn xóa toàn bộ video bị lỗi"}
            },
            "LIST_VIDEOS": {
                "description": "Hiển thị danh sách các video đã sản xuất kèm trạng thái và nút xem/tải",
                "parameters": {"page": "Số trang (mặc định 1)", "filter": "Tất cả hoặc lọc theo trạng thái"}
            },
            "GET_VIDEO": {
                "description": "Tải và gửi file video MP4 trực tiếp vào khung chat Telegram để xem hoặc download",
                "parameters": {"job_id": "Mã số Video ID cần tải"}
            },
            "SHOW_STATUS": {
                "description": "Xem trạng thái hệ thống, tiến độ render, hàng đợi tác vụ và tài nguyên",
                "parameters": {}
            },
            "SHOW_KPI": {
                "description": "Xem báo cáo KPI, tiến độ hoàn thành chỉ tiêu đăng video của các kênh",
                "parameters": {}
            },
            "SHOW_CHANNELS": {
                "description": "Xem danh sách các kênh YouTube, kênh đang chọn hoặc chuyển đổi kênh hoạt động",
                "parameters": {"target_channel_id": "Mã kênh muốn chuyển sang (tùy chọn)"}
            },
            "SHOW_SCHEDULE": {
                "description": "Xem lịch phát sóng tự động các khe giờ vàng của kênh",
                "parameters": {}
            },
            "SHOW_WEB": {
                "description": "Lấy link truy cập Web Dashboard Studio",
                "parameters": {}
            },
            "SEARCH_MEDIA": {
                "description": "Tìm kiếm kho video/ảnh stock (Pexels, Pixabay, NASA) theo từ khóa",
                "parameters": {"query": "Từ khóa tìm kiếm", "media_type": "video hoặc image"}
            },
            "VIRAL_IDEAS": {
                "description": "Tìm kiếm ý tưởng đề xuất Shorts đang thịnh hành (Trending/Viral)",
                "parameters": {"niche": "Ngách chủ đề (tùy chọn)"}
            },
            "CHAT_ASSIST": {
                "description": "Tư vấn nội dung, giải đáp thắc mắc, hướng dẫn sử dụng hoặc trò chuyện sáng tạo",
                "parameters": {"answer": "Nội dung trả lời trực tiếp cho người dùng"}
            },
            "CONNECT_COLAB": {
                "description": "Kết nối hoặc xem trạng thái Colab GPU Render Farm (NVIDIA GPU 20s/video)",
                "parameters": {"url": "Đường dẫn Cloudflare Tunnel Colab (nếu có)"}
            }
        }

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG RAG: THU THẬP TOÀN BỘ NGỮ CẢNH HỆ THỐNG ĐỘNG
    # ──────────────────────────────────────────────────────────────────────────
    def retrieve_system_context(self, user_id: int, query: str) -> Dict[str, Any]:
        """
        RAG Context Retrieval: Lấy trạng thái thời gian thực từ Database, Channels và Server
        để cung cấp cho AI ra quyết định chính xác.
        """
        context_data = {}

        # 1. Kênh & Người dùng
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user_id).first()
            active_ch_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_maya"

            channels_cfg = settings.get_channels_config()
            ch_list_summary = []
            active_ch_info = None
            for c in channels_cfg:
                cid = c.get("id")
                cname = c.get("name", cid)
                cniche = c.get("niche", "general")
                clang = c.get("language", "en")
                is_cur = (cid == active_ch_id)
                summary_item = f"• {cname} (ID: `{cid}` | Niche: {cniche} | Lang: {clang})" + (" [ĐANG CHỌN]" if is_cur else "")
                ch_list_summary.append(summary_item)
                if is_cur:
                    active_ch_info = {"id": cid, "name": cname, "niche": cniche, "language": clang}

            context_data["active_channel"] = active_ch_info or {"id": active_ch_id, "name": active_ch_id, "niche": "general", "language": "en"}
            context_data["channels_list"] = ch_list_summary

            # 2. Truy xuất video liên quan từ Database (RAG Search)
            # - Tìm số ID nếu người dùng nhắc đến số
            id_matches = re.findall(r"\b\d+\b", query)
            target_ids = [int(x) for x in id_matches if int(x) < 100000]

            relevant_jobs = []
            if target_ids:
                jobs_by_id = db.query(VideoJob).filter(VideoJob.id.in_(target_ids)).all()
                relevant_jobs.extend(jobs_by_id)

            # - Tìm theo từ khóa trong tiêu đề/chủ đề
            clean_words = [w for w in re.split(r"\s+", query.strip()) if len(w) >= 3 and not w.isdigit()]
            if clean_words:
                search_term = "%" + clean_words[0] + "%"
                jobs_by_kw = db.query(VideoJob).filter(
                    (VideoJob.title.ilike(search_term)) | (VideoJob.topic.ilike(search_term))
                ).limit(5).all()
                for j in jobs_by_kw:
                    if j not in relevant_jobs:
                        relevant_jobs.append(j)

            # - Nếu query hỏi về lỗi/failed
            if any(w in query.lower() for w in ["lỗi", "failed", "hỏng", "thất bại"]):
                failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").limit(5).all()
                for j in failed_jobs:
                    if j not in relevant_jobs:
                        relevant_jobs.append(j)

            # - Lấy thêm 8 video mới nhất gần đây để AI nắm tổng thể
            recent_jobs = db.query(VideoJob).order_by(VideoJob.id.desc()).limit(8).all()
            for j in recent_jobs:
                if j not in relevant_jobs:
                    relevant_jobs.append(j)

            jobs_summary = []
            for j in relevant_jobs[:12]:
                title = j.title or j.topic or f"Job #{j.id}"
                has_file = "Có file MP4" if (j.video_path and os.path.exists(j.video_path)) else "Chưa có file"
                jobs_summary.append(
                    f"- Video #{j.id}: '{title[:45]}' | Kênh: {j.target_channel_id} | Trạng thái: {j.status} | {has_file}"
                )
            context_data["relevant_videos"] = jobs_summary

            # 3. Thống kê hàng đợi
            total_jobs = db.query(VideoJob).count()
            rendering_count = db.query(VideoJob).filter(VideoJob.status == "RENDERING").count()
            ready_count = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()
            failed_count = db.query(VideoJob).filter(VideoJob.status == "FAILED").count()
            context_data["queue_status"] = (
                f"Tổng số: {total_jobs} video | Đang render: {rendering_count} | Chờ render: {ready_count} | Lỗi: {failed_count}"
            )

        # 4. Mạng & Web Dashboard
        port = int(os.getenv("PORT", 8000))
        web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
        context_data["web_urls"] = {
            "local": f"http://localhost:{port}",
            "url": web_url
        }

        return context_data

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG FAST HEURISTICS: PHẢN XẠ NHANH 0MS CHO CÁC LỆNH RÕ RÀNG
    # ──────────────────────────────────────────────────────────────────────────
    def match_fast_heuristics(self, query: str) -> Optional[Dict[str, Any]]:
        """Nhận diện nhanh các mẫu câu khẩu lệnh trực tiếp để phản hồi tức thì mà không cần chờ LLM."""
        q_lower = query.strip().lower()

        # 1. Khẩu lệnh XÓA VIDEO
        # Ví dụ: "xóa video 5", "xóa video số 8", "delete video #3", "xóa job 2"
        del_match = re.search(r'(?:xóa|xoa|delete|hủy|remove)\s+(?:video|clip|job|task)\s*(?:số|no|#)?\s*(\d+)', q_lower)
        if del_match:
            job_id = int(del_match.group(1))
            return {
                "intent": "REQUEST_DELETE_VIDEO",
                "parameters": {"job_id": job_id},
                "reply_message": f"Đã tìm thấy yêu cầu xóa Video #{job_id}. Đang tạo thông báo xác nhận an toàn..."
            }

        # Xóa tất cả video lỗi
        if any(kw in q_lower for kw in ["xóa tất cả video lỗi", "xóa hết video lỗi", "xoa tat ca video loi", "xóa video lỗi"]):
            return {
                "intent": "REQUEST_DELETE_VIDEO",
                "parameters": {"delete_all_failed": True},
                "reply_message": "Đang kiểm tra danh sách video lỗi để yêu cầu xác nhận xóa..."
            }

        # 2. Khẩu lệnh TẢI / GỬI VIDEO
        get_match = re.search(r'(?:tải|tai|gửi|gui|xem|lấy|download|get)\s+(?:file\s+)?video\s*(?:số|no|#)?\s*(\d+)', q_lower)
        if get_match:
            job_id = int(get_match.group(1))
            return {
                "intent": "GET_VIDEO",
                "parameters": {"job_id": job_id},
                "reply_message": f"Đang gửi file Video #{job_id} vào khung chat Telegram..."
            }

        # 3. Khẩu lệnh RENDER VIDEO
        render_match = re.search(r'(?:render|dựng|chạy|bắt đầu|tiến hành dựng)\s+video\s*(?:số|no|#)?\s*(\d+)', q_lower)
        if render_match:
            job_id = int(render_match.group(1))
            return {
                "intent": "RENDER_VIDEO",
                "parameters": {"job_id": job_id},
                "reply_message": f"Đang kích hoạt tiến trình dựng video cho Video #{job_id}..."
            }

        # 4. Khẩu lệnh KẾT NỐI / TRẠNG THÁI COLAB GPU FARM
        colab_url_match = re.search(r'(?:colab|gpu|render\s*farm)\s+(https?://[^\s]+)', q_lower)
        if colab_url_match:
            return {
                "intent": "CONNECT_COLAB",
                "parameters": {"url": colab_url_match.group(1)},
                "reply_message": "Đang kết nối tới Colab GPU Farm..."
            }
        if any(kw in q_lower for kw in ["trạng thái colab", "trang thai colab", "colab bật chưa", "colab online không", "kiểm tra colab", "kiem tra colab", "xem colab", "colab gpu"]):
            return {
                "intent": "CONNECT_COLAB",
                "parameters": {},
                "reply_message": "Đang kiểm tra kết nối tới Colab GPU Farm..."
            }

        # 5. Khẩu lệnh DANH SÁCH VIDEO
        if any(kw in q_lower for kw in ["danh sách video", "danh sach video", "xem danh sách", "tất cả video", "list video", "cac video hiện có", "các video đã làm"]):
            return {
                "intent": "LIST_VIDEOS",
                "parameters": {"page": 1},
                "reply_message": "Đang mở danh bạ video của hệ thống..."
            }

        # 6. Khẩu lệnh TRẠNG THÁI / HÀNG ĐỢI
        if any(kw in q_lower for kw in ["trạng thái", "trang thai", "tiến độ", "tien do", "bot đang làm gì", "hàng đợi", "status"]):
            return {
                "intent": "SHOW_STATUS",
                "parameters": {},
                "reply_message": "Đang tổng hợp trạng thái hệ thống..."
            }

        # 7. Khẩu lệnh LINK WEB DASHBOARD
        if any(kw in q_lower for kw in ["link web", "vào web", "mở web", "địa chỉ web", "wifi web", "dashboard"]):
            return {
                "intent": "SHOW_WEB",
                "parameters": {},
                "reply_message": "Đang lấy địa chỉ phát sóng Web Dashboard và Wi-Fi..."
            }

        # 8. Khẩu lệnh KPI
        if any(kw in q_lower for kw in ["báo cáo kpi", "kpi hôm nay", "báo cáo", "thống kê kpi", "tiến độ kpi"]):
            return {
                "intent": "SHOW_KPI",
                "parameters": {},
                "reply_message": "Đang xuất báo cáo chỉ tiêu KPI kênh..."
            }

        # 9. Khẩu lệnh XEM / ĐỔI KÊNH
        if any(kw in q_lower for kw in ["danh sách kênh", "kênh đang chọn", "quản lý kênh", "chọn kênh", "xem kênh"]):
            return {
                "intent": "SHOW_CHANNELS",
                "parameters": {},
                "reply_message": "Đang mở bảng điều khiển quản lý các kênh YouTube..."
            }

        # 10. Khẩu lệnh TẠO VIDEO (Cụ thể)
        create_match = re.search(r'^(?:tạo|làm|viết kịch bản|lên ý tưởng)\s+(?:cho\s+tôi\s+)?(?:1\s+|một\s+)?(?:video|clip|short|shorts|kịch bản)\s+(?:về|chu de|chủ đề)?\s*(.+)', q_lower)
        if create_match:
            topic_raw = create_match.group(1).strip()
            if len(topic_raw) > 2:
                return {
                    "intent": "CREATE_VIDEO",
                    "parameters": {"topic": topic_raw},
                    "reply_message": f"Đạo diễn AI đang bắt đầu nghiên cứu và lên kịch bản cho chủ đề: '{topic_raw}'..."
                }

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG LLM INTENT PARSING: PHÂN TÍCH Ý ĐỊNH SÂU BẰNG GEMINI
    # ──────────────────────────────────────────────────────────────────────────
    def parse_intent_with_llm(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Sử dụng Gemini kết hợp thông tin RAG để phân tích chính xác ý định và trích xuất tham số."""
        import requests
        import time

        tools_catalog_desc = "\n".join([
            f"- {k}: {v['description']}. Tham số: {json.dumps(v['parameters'], ensure_ascii=False)}"
            for k, v in self.tool_registry.items()
        ])

        system_prompt = f"""Bạn là AI TỔNG QUẢN & ĐẠO DIỄN ĐIỀU HÀNH cao cấp của studio sản xuất video YouTube Shorts tự động (AI YouTube Studio).
Nhiệm vụ của bạn là đọc yêu cầu bằng ngôn ngữ tự nhiên của người dùng, kết hợp với THÔNG TIN TRẠNG THÁI HỆ THỐNG HIỆN TẠI (RAG), sau đó chọn đúng CÔNG CỤ (intent) cần thực thi và phản hồi thông minh, chuyên nghiệp bằng tiếng Việt.

=== THÔNG TIN NGỮ CẢNH HỆ THỐNG HIỆN TẠI (LIVE RAG CONTEXT) ===
1. Kênh đang hoạt động của người dùng:
   - ID: {context['active_channel'].get('id')}
   - Tên: {context['active_channel'].get('name')}
   - Chủ đề (Niche): {context['active_channel'].get('niche')}
   - Ngôn ngữ: {context['active_channel'].get('language')}

2. Danh sách tất cả các kênh YouTube trong hệ thống:
{chr(10).join(context['channels_list'])}

3. Các video gần đây / liên quan trong Database SQLite:
{chr(10).join(context['relevant_videos']) if context['relevant_videos'] else "Chưa có video nào."}

4. Trạng thái hàng đợi render:
   {context['queue_status']}

5. Địa chỉ Web Studio:
   - Dashboard: {context['web_urls']['url']}

=== DANH MỤC CÔNG CỤ (TOOLS) BẠN CÓ THỂ ĐIỀU KHIỂN ===
{tools_catalog_desc}

=== QUY TẮC PHÂN TÍCH ===
1. Khi người dùng muốn XÓA video (ví dụ: "xóa video 5", "xóa video về mèo", "xóa bớt video lỗi", "xóa task 1"):
   -> Chọn intent "REQUEST_DELETE_VIDEO".
   -> Nếu họ nói tên chủ đề thay vì số ID, hãy tìm trong danh sách video liên quan ở trên để suy ra job_id tương ứng!
   -> Luôn ghi rõ job_id nếu xác định được.
2. Khi người dùng muốn TẠO video mới hoặc viết kịch bản (ví dụ: "làm video về tam giác bermuda", "lên 1 short về tâm lý", "tạo video về mèo hoang"):
   -> Chọn intent "CREATE_VIDEO".
   -> Trích xuất topic sạch sẽ, không chứa từ rườm rà.
3. Khi người dùng muốn RENDER hoặc DỰNG video (ví dụ: "dựng video 3", "render video 5"):
   -> Chọn intent "RENDER_VIDEO" kèm job_id.
4. Khi người dùng muốn XEM / TẢI video vào Telegram:
   -> Chọn intent "GET_VIDEO" kèm job_id.
5. Khi người dùng muốn XEM DANH SÁCH video:
   -> Chọn intent "LIST_VIDEOS".
6. Khi người dùng chỉ hỏi han, xin lời khuyên, thảo luận ý tưởng, hỏi cách dùng hệ thống:
   -> Chọn intent "CHAT_ASSIST", viết câu trả lời đầy đủ, sáng tạo, thực tế với vai trò Đạo diễn Shorts giàu kinh nghiệm.

BẮT BUỘC TRẢ VỀ ĐÚNG ĐỊNH DẠNG JSON DUY NHẤT (không bọc trong markdown hoặc text khác):
{{
  "thought": "Phân tích ngắn gọn suy nghĩ",
  "intent": "TÊN_CÔNG_CỤ",
  "parameters": {{}},
  "reply_message": "Lời nhắn hoặc phản hồi thân thiện, súc tích bằng tiếng Việt gửi tới người dùng"
}}
"""

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n=== YÊU CẦU CỦA NGƯỜI DÙNG ===\n\"{query}\""}]}
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json"
            }
        }

        # Gọi Gemini qua KeyRotator
        start_time = time.monotonic()
        for _ in range(len(self.rotator.keys) * 2 or 2):
            key = self.rotator.get_key()
            if not key:
                break
            for model in AVAILABLE_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                try:
                    resp = requests.post(url, json=payload, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            # Parse JSON
                            clean_json = raw_text.strip()
                            if clean_json.startswith("```"):
                                clean_json = re.sub(r"^```(?:json)?\n?", "", clean_json)
                                clean_json = re.sub(r"\n?```$", "", clean_json)
                            parsed = json.loads(clean_json.strip())
                            if "intent" in parsed:
                                return parsed
                    elif resp.status_code in [429, 403]:
                        break  # Key hết quota, chuyển sang key kế tiếp
                except Exception as e:
                    logger.debug(f"Gemini LLM Intent parsing error with {model}: {e}")

        # Fallback an toàn nếu LLM lỗi mạng
        return {
            "intent": "CHAT_ASSIST",
            "parameters": {},
            "reply_message": f"Dạ, em đã nhận được yêu cầu: \"{query}\". Đạo diễn AI đang xử lý theo ngữ cảnh kênh hiện tại của bạn!"
        }

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG THỰC THI (EXECUTION): ĐIỀU PHỐI ĐÚNG HANDLER & BẢO ĐẢM AN TOÀN
    # ──────────────────────────────────────────────────────────────────────────
    async def process_and_execute(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raw_query: str,
        status_message=None
    ) -> bool:
        """
        Tiếp nhận yêu cầu tự nhiên, xác định ý định qua RAG + LLM/Heuristics
        và kích hoạt đúng nghiệp vụ tương ứng.
        """
        user = update.effective_user
        user_id = user.id if user else 0
        username = user.username or user.first_name if user else "Creator"

        # 1. Thử Fast Heuristics trước để phản hồi 0ms
        decision = self.match_fast_heuristics(raw_query)

        # 2. Nếu chưa rõ ràng, dùng RAG + LLM phân tích sâu
        if not decision:
            system_ctx = self.retrieve_system_context(user_id, raw_query)
            decision = await asyncio.to_thread(self.parse_intent_with_llm, raw_query, system_ctx)

        intent = decision.get("intent", "CHAT_ASSIST")
        params = decision.get("parameters", {})
        reply_msg = decision.get("reply_message", "")

        logger.info(f"AICommander [User: {username}] Intent: {intent} | Params: {params}")

        # 3. Điều phối thực thi theo Intent
        from app.bot.handlers import safe_reply, safe_edit_text

        # ──────────────────────────────────────────────────────────────────────
        # A. INTENT: XÓA VIDEO (BẮT BUỘC BƯỚC XÁC NHẬN AN TOÀN)
        # ──────────────────────────────────────────────────────────────────────
        if intent == "REQUEST_DELETE_VIDEO":
            # Trường hợp 1: Xóa hàng loạt tất cả video lỗi
            if params.get("delete_all_failed"):
                with get_db() as db:
                    failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").all()
                    count = len(failed_jobs)
                if count == 0:
                    text = "ℹ️ Hiện tại không có video nào ở trạng thái lỗi (`FAILED`). Không cần xóa dọn dẹp!"
                    if status_message:
                        await safe_edit_text(status_message, text)
                    else:
                        await safe_reply(update, text)
                    return True

                text = (
                    f"⚠️ **XÁC NHẬN XÓA HÀNG LOẠT ({count} VIDEO LỖI)**\n\n"
                    f"Hệ thống tìm thấy **{count} video** đang bị lỗi (`FAILED`).\n"
                    f"Bạn có chắc chắn muốn xóa toàn bộ các video lỗi này khỏi Database và ổ cứng không?\n\n"
                    f"⚠️ *Hành động này không thể hoàn tác!*"
                )
                markup = get_delete_failed_confirmation_keyboard(count)
                if status_message:
                    await safe_edit_text(status_message, text, reply_markup=markup)
                else:
                    await safe_reply(update, text, reply_markup=markup)
                return True

            # Trường hợp 2: Xóa video cụ thể theo ID hoặc từ khóa
            target_job_id = params.get("job_id")
            if not target_job_id:
                # Cố gắng tìm video theo từ khóa trong DB
                query_term = params.get("query") or raw_query
                with get_db() as db:
                    matched = db.query(VideoJob).filter(
                        (VideoJob.title.ilike(f"%{query_term}%")) | (VideoJob.topic.ilike(f"%{query_term}%"))
                    ).order_by(VideoJob.id.desc()).first()
                    if matched:
                        target_job_id = matched.id

            if not target_job_id:
                text = (
                    f"🔍 **Chưa xác định được video cần xóa:**\n\n"
                    f"Bạn muốn xóa video nào? Hãy nói rõ mã số (Ví dụ: `xóa video 5`) hoặc gõ tiêu đề video nhé!"
                )
                if status_message:
                    await safe_edit_text(status_message, text)
                else:
                    await safe_reply(update, text)
                return True

            # Lấy thông tin video để tạo Phiếu Xác Nhận
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == int(target_job_id)).first()
                if not job:
                    text = f"❌ Không tìm thấy **Video #{target_job_id}** trong cơ sở dữ liệu."
                    if status_message:
                        await safe_edit_text(status_message, text)
                    else:
                        await safe_reply(update, text)
                    return True

                job_title = job.title or job.topic or f"Video #{job.id}"
                job_ch = job.target_channel_id
                job_st = job.status
                created_str = job.created_at.strftime("%d/%m/%Y %H:%M") if job.created_at else "Không rõ"
                has_file = "✅ Có file MP4 trên ổ cứng" if (job.video_path and os.path.exists(job.video_path)) else "⚪ Chưa xuất file MP4"

            # Hiển thị Modal / Card cảnh báo xác nhận với 2 nút [Xác nhận xóa] và [Hủy bỏ]
            text = (
                f"⚠️ **XÁC NHẬN XÓA VIDEO VĨNH VIỄN**\n\n"
                f"Bạn có chắc chắn muốn xóa video này không?\n\n"
                f"🎬 **Task ID:** `#{target_job_id}`\n"
                f"📌 **Tiêu đề:** {job_title}\n"
                f"🎯 **Kênh:** `{job_ch}`\n"
                f"📊 **Trạng thái:** `{job_st}`\n"
                f"🕒 **Ngày tạo:** {created_str}\n"
                f"📁 **Lưu trữ:** {has_file}\n\n"
                f"⚠️ *LƯU Ý: Thao tác này sẽ xóa vĩnh viễn dữ liệu trong Database và toàn bộ file video MP4, âm thanh trên máy chủ! Không thể khôi phục sau khi xóa.*"
            )
            markup = get_delete_confirmation_keyboard(int(target_job_id))
            if status_message:
                await safe_edit_text(status_message, text, reply_markup=markup)
            else:
                await safe_reply(update, text, reply_markup=markup)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # B. INTENT: TẠO VIDEO MỚI / KỊCH BẢN
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "CREATE_VIDEO":
            topic = params.get("topic") or raw_query
            notes = params.get("director_notes")
            target_ch = params.get("channel_id")
            if target_ch and context and context.user_data is not None:
                context.user_data["target_channel_id"] = target_ch
            if notes:
                topic = f"{topic} | {notes}"

            if status_message:
                await safe_edit_text(status_message, f"🎬 Đạo diễn AI đang chuẩn bị kịch bản cho: *\"{topic}\"*...")

            from app.bot.handlers import idea_command
            context.args = topic.split()
            await idea_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # C. INTENT: RENDER DỰNG VIDEO
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "RENDER_VIDEO":
            job_id = params.get("job_id")
            if not job_id:
                # Thử tìm số từ câu hỏi
                m = re.search(r'\b\d+\b', raw_query)
                job_id = int(m.group(0)) if m else None

            if not job_id:
                text = "💡 Vui lòng chỉ định rõ mã số video bạn muốn render (Ví dụ: `render video 3`)."
                if status_message:
                    await safe_edit_text(status_message, text)
                else:
                    await safe_reply(update, text)
                return True

            from app.bot.handlers import render_command
            context.args = [str(job_id)]
            await render_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # D. INTENT: TẢI / GỬI FILE VIDEO VÀO TELEGRAM
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "GET_VIDEO":
            job_id = params.get("job_id")
            if not job_id:
                m = re.search(r'\b\d+\b', raw_query)
                job_id = int(m.group(0)) if m else None

            if not job_id:
                text = "💡 Bạn muốn tải video số mấy? (Ví dụ: `tải video 5`)."
                if status_message:
                    await safe_edit_text(status_message, text)
                else:
                    await safe_reply(update, text)
                return True

            from app.bot.handlers import send_video_file_handler
            await send_video_file_handler(update, context, int(job_id))
            return True

        # ──────────────────────────────────────────────────────────────────────
        # E. INTENT: DANH SÁCH VIDEO
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "LIST_VIDEOS":
            from app.bot.handlers import _show_videos_list
            await _show_videos_list(update, context, page=1)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # F. INTENT: XEM TRẠNG THÁI HỆ THỐNG / HÀNG ĐỢI
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SHOW_STATUS":
            from app.bot.handlers import status_command
            await status_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # G. INTENT: BÁO CÁO KPI
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SHOW_KPI":
            from app.bot.handlers import kpi_command
            await kpi_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # H. INTENT: DANH SÁCH / QUẢN LÝ KÊNH
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SHOW_CHANNELS":
            from app.bot.handlers import channels_command
            await channels_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # I. INTENT: LINK WEB DASHBOARD & WI-FI
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SHOW_WEB":
            from app.bot.handlers import web_command
            await web_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # J. INTENT: LỊCH ĐĂNG BÀI
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SHOW_SCHEDULE":
            from app.bot.handlers import schedule_command
            await schedule_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # K. INTENT: TÌM KIẾM MEDIA STOCK
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "SEARCH_MEDIA":
            m_type = params.get("media_type", "video")
            q = params.get("query") or raw_query
            context.args = q.split()
            if m_type == "image":
                from app.bot.handlers import search_image_command
                await search_image_command(update, context)
            else:
                from app.bot.handlers import search_video_command
                await search_video_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # L. INTENT: Ý TƯỞNG VIRAL TRENDING
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "VIRAL_IDEAS":
            from app.bot.handlers import viral_command
            await viral_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # M. INTENT: KẾT NỐI / TRẠNG THÁI COLAB GPU FARM
        # ──────────────────────────────────────────────────────────────────────
        elif intent == "CONNECT_COLAB":
            from app.bot.handlers import colab_command
            url = params.get("url")
            context.args = [url] if url else []
            await colab_command(update, context)
            return True

        # ──────────────────────────────────────────────────────────────────────
        # N. INTENT: TRÒ CHUYỆN / TƯ VẤN SÁNG TẠO (CHAT_ASSIST)
        # ──────────────────────────────────────────────────────────────────────
        else:
            session_id = str(update.effective_chat.id) if update.effective_chat else "default"
            markup = get_chat_brainstorm_keyboard(session_id)
            final_text = f"🎬 **ĐẠO DIỄN SHORTS AI:**\n\n{reply_msg}"
            if status_message:
                await safe_edit_text(status_message, final_text, reply_markup=markup)
            else:
                await safe_reply(update, final_text, reply_markup=markup)
            return True


# Singleton instance toàn hệ thống
ai_commander = AICommander()
