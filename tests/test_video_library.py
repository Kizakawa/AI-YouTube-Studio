"""Tests cho Thư Viện Video Đã Đăng (không chạm DB thật / YouTube thật)."""
from app.services import video_library as vl


def _vid(title, tags=(), cluster="", summary=""):
    return {
        "id": 1, "channel_id": "c1", "channel_name": "Kênh A", "youtube_video_id": "abc",
        "youtube_url": "https://youtu.be/abc", "title": title, "published_at": "2026-10-01",
        "views": 10, "source": "system", "summary": summary,
        "topic_tags": list(tags), "topic_cluster": cluster,
    }


def test_duplicate_detects_similar_topic(monkeypatch):
    monkeypatch.setattr(vl, "list_videos", lambda **kw: [
        _vid("Why Uncanny Valley Creeps You Out", ["uncanny valley", "robot"], "Uncanny Valley"),
        _vid("Deepest Ocean Trench Secrets", ["ocean"], "Deep Sea"),
    ])
    res = vl.check_duplicate("c1", "The creepy uncanny valley robot effect")
    assert res and res[0]["title"].startswith("Why Uncanny")
    assert all("Ocean" not in r["title"] for r in res)


def test_duplicate_ignores_unrelated_topic(monkeypatch):
    monkeypatch.setattr(vl, "list_videos", lambda **kw: [_vid("Deepest Ocean Trench Secrets", ["ocean"])])
    assert vl.check_duplicate("c1", "Dark psychology of manipulation tricks") == []


def test_fallback_summary_strips_links_and_truncates():
    s = vl._fallback_summary("T", "Hello https://x.com/a #tag " + "word " * 100)
    assert "http" not in s and "#tag" not in s and s.endswith("…")
    assert vl._fallback_summary("Title", "") == "Title"


def test_format_library_text_empty_and_filled():
    assert "Chưa có video" in vl.format_library_text([])
    out = vl.format_library_text([_vid("Hello", summary="Tóm tắt")], "HEAD")
    assert "HEAD" in out and "Kênh A" in out and "Tóm tắt" in out and "https://youtu.be/abc" in out
