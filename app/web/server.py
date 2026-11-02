"""
app.web.server — Điểm khởi chạy Web Dashboard FastAPI (mô-đun hóa qua `app/web/routers/`).

Cấu trúc các module router:
- `core_and_dashboard.py`: Khởi tạo FastAPI `app`, Middleware bảo mật, Health-check & Thống kê tổng quan.
- `videos_and_scripts.py`: Quản lý Pipeline Video, Kịch bản, Storyboard, Render & Xuất bản YouTube/Drive.
- `accounts_and_oauth.py`: Quản lý Đa tài khoản Google, OAuth2, Kênh Thương hiệu & Hồ sơ Ngách.
- `repurpose_and_batch.py`: Tái chế Video Dài thành Shorts (Repurposer) & Sản xuất Hàng loạt (Batch Producer).
- `analytics_and_autopilot.py`: YouTube Live Analytics, Dự báo Tăng trưởng, AutoPilot & Manual Studio.
"""
from app.web.routers.core_and_dashboard import *  # noqa: F401,F403
from app.web.routers.accounts_and_oauth import *  # noqa: F401,F403
from app.web.routers.videos_and_scripts import *  # noqa: F401,F403
from app.web.routers.repurpose_and_batch import *  # noqa: F401,F403
from app.web.routers.analytics_and_autopilot import *  # noqa: F401,F403
from app.web.routers.core_and_dashboard import app

__all__ = ["app"]
