"""
app.bot.handlers — Facade điều phối các lệnh & callback Telegram Bot (mô-đun hóa qua `app/bot/modules/`).

Cấu trúc các module con:
- `core_auth_and_channels.py`: Chống spam click, xác thực quyền truy cập & quản lý danh sách kênh.
- `ideation_and_bilibili.py`: Lên ý tưởng Viral (`/viral`), tạo kịch bản (`/idea`, `/longform`) & săn Bilibili (`/bilibili`).
- `render_and_queue.py`: Hàng đợi Render, tự động khôi phục Job, chỉnh sửa kịch bản/media & xuất bản YouTube.
- `video_callbacks.py`: Xử lý toàn bộ nút bấm tương tác trên thẻ Video (duyệt, sửa, render, đăng, xóa).
- `library_autopilot_and_chat.py`: Kho video, tìm kiếm ảnh/video, lịch đăng, báo cáo KPI, AutoPilot & Chat AI.
"""
from app.bot.modules.core_auth_and_channels import *  # noqa: F401,F403
from app.bot.modules.core_auth_and_channels import (
    _ActionPermit,
    _try_acquire_action,
    _release_action,
    _find_recent_duplicate_job,
    _format_channel_info_text,
    _parse_idea_input,
    _extract_video_format_from_input,
)
from app.bot.modules.render_and_queue import *  # noqa: F401,F403
from app.bot.modules.render_and_queue import (
    _is_video_already_dispatched,
    _mark_video_dispatched,
    _CompletedJobResult,
    _is_job_already_completed_in_db,
    _execute_render_task,
    _enqueue_render,
    _handle_script_revision,
    _execute_channel_publish,
)
from app.bot.modules.ideation_and_bilibili import *  # noqa: F401,F403
from app.bot.modules.ideation_and_bilibili import _execute_bilibili_search_and_reply
from app.bot.modules.video_callbacks import *  # noqa: F401,F403
from app.bot.modules.video_callbacks import (
    _handle_confirm_delete_video,
    _handle_cancel_delete_video,
    _handle_confirm_delete_failed_videos,
)
from app.bot.modules.library_autopilot_and_chat import *  # noqa: F401,F403
from app.bot.modules.library_autopilot_and_chat import (
    _resolve_video_file_path,
    _show_videos_list,
    _match_channel_from_text,
    _send_library_answer,
    _show_schedule_view,
    _show_autopilot_view,
)
