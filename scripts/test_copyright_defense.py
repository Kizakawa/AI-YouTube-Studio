"""
Kiểm tra toàn diện cơ chế Chống Gậy Bản Quyền & Attribution Video Gốc:
1. Tạo mô tả kèm Full Video Source Link, Original Creator Credit & Section 107 Fair Use.
2. Kiểm tra Watermark Overlay trên video (Top center, mờ dịu, chuẩn font).
3. Kiểm tra bộ lọc âm thanh và màu sắc Anti-Content-ID trong FFmpeg command.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.monetization_engine import build_repurposed_video_description
from app.services.video_repurposer import repurposer_service
from app.utils.subtitle_helper import SubtitleHelper

def test_description_copyright_attribution():
    print("=== [1] Testing Full Video Link & Section 107 Fair Use Disclaimer ===")
    source_url = "https://www.youtube.com/watch?v=K7SR0_rhMSo&feature=youtu.be"
    creator = "BBC Earth Official"
    
    desc = build_repurposed_video_description(
        title="Apex Predator Lethal Strike in the Savanna",
        niche_id="wildlife_predators",
        source_url=source_url,
        author=creator,
        mode="vietnam"
    )
    
    print("\n--- GENERATED DESCRIPTION PREVIEW ---")
    print(desc)
    print("--------------------------------------\n")
    
    # 1. Full video attribution & creator
    assert "Original footage & full story" in desc, "Thiếu mục Original footage & full story"
    assert "BBC Earth Official" in desc, "Thiếu tên kênh gốc (author)"
    assert "https://www.youtube.com/watch?v=K7SR0_rhMSo" in desc, "Thiếu link video gốc"
    
    # 2. Section 107 Fair Use Disclaimer
    assert "⚖️ FAIR USE DISCLAIMER:" in desc, "Thiếu tiêu đề Fair Use Disclaimer"
    assert "Section 107 of the US Copyright Act 1976" in desc, "Thiếu điều khoản Section 107"
    assert "All rights belong to their respective owners" in desc, "Thiếu cam kết quyền sở hữu tác giả gốc"
    assert "removal requests" in desc, "Thiếu thông tin liên hệ removal requests"
    
    # 3. Affiliate monetization integration with Store ID: kizakawa-20 (Amazon Only)
    assert "kizakawa-20" in desc, "Thiếu Store ID kizakawa-20 trong mô tả!"
    assert "tag=kizakawa-20" in desc, "Thiếu tag=kizakawa-20 trong link Amazon!"
    assert "amazon.com" in desc.lower(), "Thiếu link Amazon trong mô tả!"
    assert "lazada" not in desc.lower(), "Vẫn còn xuất hiện Lazada trong mô tả!"
    
    print("✅ TEST 1 PASSED: Full Video Source Link, Original Creator Credit & Pure Amazon Store ID kizakawa-20 Verified!")

def test_pinned_comment_with_store_id():
    print("\n=== [1b] Testing Pinned Comment with Store ID: kizakawa-20 (Amazon Only) ===")
    from app.services.monetization_engine import build_repurposed_pinned_comment
    
    comment = build_repurposed_pinned_comment(
        title="Apex Predator Lethal Strike in the Savanna",
        niche_id="wildlife_predators",
        source_url="https://www.youtube.com/watch?v=K7SR0_rhMSo",
        author="BBC Earth Official",
        tracking_tag="kizakawa-20"
    )
    
    print("\n--- GENERATED PINNED COMMENT PREVIEW ---")
    print(comment)
    print("----------------------------------------\n")
    
    assert "https://www.youtube.com/watch?v=K7SR0_rhMSo" in comment, "Thiếu link video gốc trong bình luận ghim"
    assert "BBC Earth Official" in comment, "Thiếu tác giả trong bình luận ghim"
    assert "kizakawa-20" in comment, "Thiếu Store ID kizakawa-20 trong bình luận ghim"
    assert "tag=kizakawa-20" in comment, "Thiếu tag=kizakawa-20 trong link Amazon ở bình luận ghim"
    assert "amazon.com" in comment.lower(), "Thiếu link Amazon trong bình luận ghim!"
    assert "lazada" not in comment.lower(), "Vẫn còn xuất hiện Lazada trong bình luận ghim!"
    
    print("✅ TEST 1b PASSED: Pinned Comment with Pure Amazon Store ID kizakawa-20 Verified!")

def test_watermark_ass_generation():
    print("\n=== [2] Testing Watermark Credit Overlay in SubtitleHelper ===")
    from app.schemas.contracts import SceneContract
    
    mock_scenes = [
        SceneContract(scene_id=1, narration="A fast-paced hunting scene in the wild", duration=52.0, visual_keyword="nature wild")
    ]
    
    ass_content = SubtitleHelper.build_ass_content(
        scenes=mock_scenes,
        credit_text="BBC Earth • Full Video in Desc"
    )
    
    # Kiểm tra WatermarkStyle được định nghĩa trong header
    assert "Style: WatermarkStyle" in ass_content, "Thiếu định nghĩa WatermarkStyle trong ASS header"
    assert "WatermarkStyle,Arial,28" in ass_content, "WatermarkStyle định dạng không đúng"
    
    # Kiểm tra Dialogue event của watermark
    assert "WatermarkStyle" in ass_content, "Thiếu Dialogue event cho WatermarkStyle"
    assert "BBC Earth • Full Video in Desc" in ass_content, "Watermark text không khớp format mong muốn"
    
    print("✅ TEST 2 PASSED: Watermark Style & Dialogue Event Successfully Embedded into ASS Subtitles!")

def test_anti_content_id_filters():
    print("\n=== [3] Testing Anti-Content-ID Visual & Audio Filters in Repurposer ===")
    import inspect
    
    # Kiểm tra mã nguồn render_repurposed_short để đảm bảo filter tồn tại
    source = inspect.getsource(repurposer_service.render_repurposed_short)
    
    # Filter hình ảnh (màu sắc/tương phản để lệch histogram robot)
    assert "eq=contrast=1.03:brightness=0.01:saturation=1.04" in source, "Thiếu bộ lọc Visual Anti-Content-ID"
    
    # Filter âm thanh (micro pitch-shift 1.5% + tempo preservation)
    assert "asetrate=44100*1.015,atempo=1/1.015,aresample=44100" in source, "Thiếu bộ lọc Audio Anti-Content-ID Pitch/Tempo"
    
    # Audio fade in/out
    assert "afade=t=in:ss=0:d=0.25" in source, "Thiếu bộ lọc Audio Fade-In"
    assert "afade=t=out" in source, "Thiếu bộ lọc Audio Fade-Out"
    
    # Auto effective_credit
    assert "effective_credit = credit_text" in source, "Thiếu logic gán effective_credit"
    assert "Full Video in Desc" in source, "Thiếu dòng chữ Full Video in Desc"
    
    print("✅ TEST 3 PASSED: Anti-Content-ID Visual & Audio Transformations Verified!")

if __name__ == "__main__":
    test_description_copyright_attribution()
    test_pinned_comment_with_store_id()
    test_watermark_ass_generation()
    test_anti_content_id_filters()
    print("\n🎉 ALL COPYRIGHT DEFENSE & STORE ID KIZAKAWA-20 TESTS PASSED 100%! 🎉")
