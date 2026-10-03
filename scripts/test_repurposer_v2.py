"""
Test script for Repurposer V2.1 features:
1. Professional Native English Viral Title rewriting & Vietnamese filtering.
2. Auto-description generation with Lazada Affiliate product links.
3. DELETE /api/repurposer/clips/{filename} API endpoint.
4. Metadata resolution backward compatibility.
"""

import os
import sys
import io
import json
import time
import requests
from pathlib import Path

# Force utf-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_URL = "http://127.0.0.1:8000"

def test_title_cleansing():
    print("=== [1] Testing Viral English Title Cleansing & Niche DNA ===")
    from app.services.video_repurposer import repurposer_service

    test_cases = [
        ("Chó con bị bỏ rơi ngoài đường trong mưa bão", "animal_rescue"),
        ("Sư tử đại chiến linh cẩu tranh giành mồi", "wildlife_predators"),
        ("3 dấu hiệu bạn đang bị thao túng tâm lý", "psychology"),
        ("Bí ẩn âm thanh kỳ lạ dưới đáy rãnh Mariana", "deep_sea_earth_mysteries"),
        ("Video tổng hợp những điều kỳ thú", "general"),
    ]

    vietnamese_chars = "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ"

    for raw_title, niche in test_cases:
        cleaned = repurposer_service._ensure_viral_english_title(raw_title, niche)
        print(f"[{niche}] Raw: '{raw_title}' -> Cleaned: '{cleaned}'")
        
        # Check no Vietnamese
        for ch in vietnamese_chars:
            assert ch not in cleaned.lower(), f"Lỗi: Ký tự tiếng Việt '{ch}' vẫn còn trong tiêu đề: {cleaned}"
        # Check #Shorts
        assert "#Shorts" in cleaned or "#shorts" in cleaned.lower(), f"Lỗi: Thiếu thẻ #Shorts: {cleaned}"
        # Check minimum length
        assert len(cleaned) >= 20, f"Lỗi: Tiêu đề quá ngắn: {cleaned}"

    print("✅ PASS: 100% Native Viral English Title Cleansing Verified!\n")


def test_lazada_auto_description():
    print("=== [2] Testing Lazada Affiliate Link & Auto-Description ===")
    from app.services.monetization_engine import get_lazada_product_for_niche, build_repurposed_video_description

    niches = ["animal_rescue", "wildlife_predators", "psychology", "deep_sea_earth_mysteries", "general"]

    for n in niches:
        prod = get_lazada_product_for_niche(n, "viral video")
        print(f"[{n}] Sản phẩm: {prod['name']} | URL: {prod['url']}")
        assert "lazada.vn" in prod["url"], f"Lỗi: URL không phải Lazada: {prod['url']}"
        assert len(prod["name"]) > 5, f"Lỗi: Tên sản phẩm quá ngắn: {prod['name']}"

        desc = build_repurposed_video_description(
            title="Heartwarming Rescue Story #Shorts",
            summary="This little puppy was completely transformed after 60 days.",
            niche_id=n,
            channel_name="Global Channel"
        )
        assert "LAZADA" in desc, "Lỗi: Mô tả thiếu khối Lazada"
        assert prod["url"] in desc, "Lỗi: Mô tả thiếu link sản phẩm"
        assert "#Shorts" in desc, "Lỗi: Mô tả thiếu hashtags"

    print("✅ PASS: Lazada Auto-Description Matrix Verified!\n")


def test_clips_api_and_metadata_resolution():
    print("=== [3] Testing /api/repurposer/clips & Metadata Resolution ===")
    res = requests.get(f"{BASE_URL}/api/repurposer/clips", timeout=10)
    assert res.status_code == 200, f"Lỗi gọi API clips: {res.status_code}"
    data = res.json()
    assert data.get("success") is True, "API clips không trả về success=True"
    clips = data.get("clips", [])
    print(f"Hiện có {len(clips)} clips trong hệ thống.")

    for c in clips:
        # Check title is resolved (not empty and not raw filename ending in .mp4)
        assert c.get("title"), f"Lỗi: Clip {c['filename']} không có title"
        assert not c["title"].endswith(".mp4"), f"Lỗi: Title vẫn hiển thị đuôi .mp4: {c['title']}"
        print(f"Clip: {c['filename']} -> Title: '{c['title']}' | Has Description: {bool(c.get('description'))}")

    print("✅ PASS: Metadata Resolution & Backward Compatibility Verified!\n")


def test_delete_clip_api():
    print("=== [4] Testing DELETE /api/repurposer/clips/{filename} API ===")
    from app.services.video_repurposer import CLIPS_DIR

    # Tạo một clip test giả lập kèm 2 định dạng file metadata
    test_filename = f"short_test_delete_{int(time.time())}.mp4"
    test_mp4 = CLIPS_DIR / test_filename
    test_meta_stem = CLIPS_DIR / f"{Path(test_filename).stem}.meta.json"
    test_meta_full = CLIPS_DIR / f"{test_filename}.meta.json"

    # Ghi file tạm
    test_mp4.write_text("dummy video content", encoding="utf-8")
    meta_payload = json.dumps({"title": "Test Delete Short #Shorts", "description": "Test Desc"}, ensure_ascii=False)
    test_meta_stem.write_text(meta_payload, encoding="utf-8")
    test_meta_full.write_text(meta_payload, encoding="utf-8")

    assert test_mp4.exists()
    assert test_meta_stem.exists()
    assert test_meta_full.exists()
    print(f"Đã tạo file test: {test_filename}")

    # Gọi DELETE API
    del_res = requests.delete(f"{BASE_URL}/api/repurposer/clips/{test_filename}", timeout=10)
    assert del_res.status_code == 200, f"Lỗi DELETE API: {del_res.status_code} - {del_res.text}"
    del_data = del_res.json()
    assert del_data.get("success") is True, f"Lỗi phản hồi: {del_data}"
    print(f"Phản hồi DELETE API: {del_data}")

    # Kiểm tra file đã bị xóa sạch
    assert not test_mp4.exists(), "Lỗi: File MP4 chưa bị xóa!"
    assert not test_meta_stem.exists(), "Lỗi: File .meta.json (stem) chưa bị xóa!"
    assert not test_meta_full.exists(), "Lỗi: File .mp4.meta.json chưa bị xóa!"
    print("Đã xác minh: Toàn bộ file MP4 và Metadata đều bị xóa sạch.")

    # Test bảo vệ Path Traversal
    bad_res = requests.delete(f"{BASE_URL}/api/repurposer/clips/../../run.py", timeout=10)
    assert bad_res.status_code in (400, 404), f"Lỗi bảo mật: API không chặn path traversal! Status: {bad_res.status_code}"
    print("Đã xác minh: Path Traversal bị chặn tuyệt đối.")

    print("✅ PASS: DELETE Clip API Verified!\n")


if __name__ == "__main__":
    try:
        test_title_cleansing()
        test_lazada_auto_description()
        test_clips_api_and_metadata_resolution()
        test_delete_clip_api()
        print("🎉🎉🎉 ALL REPURPOSER V2.1 TESTS PASSED PERFECTLY! 🎉🎉🎉")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ TEST FAILED: {e}")
        sys.exit(1)
