"""
app.agents.publisher_agent — Agent xác thực OAuth2 và xuất bản video YouTube (mô-đun hóa qua 4 Mixins).

Cấu trúc module trong `app/agents/publisher/` (~290–680 dòng/file):
- `oauth_and_upload_mixin.py`: Khởi tạo OAuth2, tự làm mới token & tải video/thumbnail lên YouTube.
- `engagement_comments_mixin.py`: Sinh & đồng bộ bình luận ghim kích hoạt tranh luận.
- `seo_tags_mixin.py`: Sinh bộ thẻ Tags/Hashtags thông minh & chuẩn hóa mô tả Shorts.
- `metadata_healer_mixin.py`: Kiểm toán, tự chữa lành Metadata & đồng bộ từ khóa thương hiệu kênh.
"""
from app.agents.publisher.engagement_comments_mixin import EngagementCommentsMixin
from app.agents.publisher.metadata_healer_mixin import MetadataHealerMixin
from app.agents.publisher.oauth_and_upload_mixin import OAuthAndUploadMixin
from app.agents.publisher.seo_tags_mixin import SeoTagsMixin


class PublisherAgent(
    OAuthAndUploadMixin,
    EngagementCommentsMixin,
    SeoTagsMixin,
    MetadataHealerMixin,
):
    """Agent chịu trách nhiệm xác thực OAuth2 và đăng tải/lên lịch video lên các kênh YouTube."""
    pass


__all__ = ["PublisherAgent"]
