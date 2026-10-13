"""
Sản xuất video mẫu PILOT: Ngách Hoạt Họa 2D Tâm Lý Học Châm Biếm (satirical_psychology_animation)
Chủ đề: "Hội Chứng Người Tốt — Tại sao càng ngoan ngoãn người khác càng đối xử tệ?"
Kiểm thử toàn diện:
1. Sinh ảnh hoạt họa 2D tối giản phong cách comic/webtoon
2. Chuyển động gật đầu hoạt họa (nod_loop) cho cảnh làm hài lòng
3. Giọng đọc tiếng Việt vi-VN-NamMinhNeural
4. Phụ đề Karaoke ASS tiếng Việt
5. Hòa âm Audio Ducking + BGM
"""
import sys
import os
import asyncio
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.schemas.contracts import SceneContract, RenderJobContract
from app.agents.image_agent import ImageAgent
from app.agents.voice_agent import VoiceAgent
from app.agents.video_agent import VideoAgent
from app.utils.subtitle_helper import SubtitleHelper
from app.utils.ffmpeg_helper import probe_file

async def main():
    print("=" * 80)
    print("🚀 BẮT ĐẦU SẢN XUẤT VIDEO PILOT: HOẠT HỌA 2D TÂM LÝ CHÂM BIẾM")
    print("=" * 80)

    work_dir = Path("storage/temp/pilot_comic_production")
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Kịch bản phân cảnh
    scenes_data = [
        {
            "scene_id": 1,
            "narration": "Bạn có bao giờ tự hỏi: Tại sao bạn càng ngoan ngoãn, biết điều... người khác lại càng đối xử tệ với bạn?",
            "visual_prompt": "Minimalist 2D satirical comic art, simple relatable character line-art, close up on tired face with forced fake polite smile, dark background, clean bold black outlines, deep emotional resonance, 9:16 vertical composition, no watermark, no text",
            "camera_motion": "zoom_in"
        },
        {
            "scene_id": 2,
            "narration": "Sếp giao việc ngoài giờ, bạn gật đầu. Bạn bè mượn tiền, bạn cười trừ. Người khác ép bạn, bạn lại tự nhận lỗi về mình.",
            "visual_prompt": "Minimalist 2D satirical comic art, simple office worker sitting at cluttered desk full of paperwork, obedient robotic expression, clean line art, subtle flat colors, 9:16 vertical composition, no watermark, no text",
            "camera_motion": "nod_loop" # Kích hoạt hiệu ứng gật đầu hoạt họa!
        },
        {
            "scene_id": 3,
            "narration": "Sự thật tàn nhẫn: Sự tử tế không có ranh giới, người ta không gọi là hiền lành, mà là tự biến mình thành kẻ yếu thế.",
            "visual_prompt": "Minimalist 2D dark comic art, character looking in bathroom mirror, holding a cracked smiling theater mask, revealing exhausted authentic face, moody dim lighting, 9:16 vertical composition, no watermark, no text",
            "camera_motion": "zoom_in"
        },
        {
            "scene_id": 4,
            "narration": "Học cách nói Không không biến bạn thành kẻ xấu, nó chỉ nhắc người khác nhớ rằng: Bạn cũng là một con người!",
            "visual_prompt": "Minimalist 2D satirical comic art, confident empowered character standing firm, tossing the fake mask into trash bin, sharp determined eyes, cinematic rim lighting, 9:16 vertical composition, no watermark, no text",
            "camera_motion": "zoom_out"
        }
    ]

    # 2. Sinh giọng đọc tiếng Việt bằng VoiceAgent
    print("\n🎙️ [BƯỚC 1/4] Đang tạo giọng đọc tiếng Việt (vi-VN-NamMinhNeural)...")
    voice_agent = VoiceAgent()
    scenes = [
        SceneContract(
            scene_id=s["scene_id"],
            narration=s["narration"],
            visual_keyword=f"scene_{s['scene_id']}",
            visual_prompt=s["visual_prompt"],
            camera_motion=s["camera_motion"],
            resolved_camera_motion=s["camera_motion"],
            media_type="image"
        )
        for s in scenes_data
    ]

    audio_dir = str(work_dir / "audio")
    enriched_scenes = await voice_agent.generate_per_scene_audio(
        scenes=scenes,
        output_dir=audio_dir,
        voice="vi-VN-NamMinhNeural",
        rate="-2%"
    )

    full_audio_path = str(work_dir / "full_narration.mp3")
    total_ms = await voice_agent.concat_scene_audio(enriched_scenes, full_audio_path)
    audio_info = probe_file(full_audio_path)
    total_duration = float(audio_info.get("format", {}).get("duration", 0)) if audio_info else 0
    print(f"✅ Giọng đọc hoàn tất: {full_audio_path} (Tổng thời lượng: {total_duration:.1f}s)")

    # 3. Sinh hình ảnh AI hoạt họa 2D tối giản
    print("\n🎨 [BƯỚC 2/4] Đang chuẩn bị hình ảnh hoạt họa 2D phong cách comic tối giản...")
    image_agent = ImageAgent()
    images_dir = work_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    for sc in enriched_scenes:
        img_dest = str(images_dir / f"scene_{sc.scene_id:03d}.jpg")
        if Path(img_dest).exists() and Path(img_dest).stat().st_size > 1024:
            print(f"  -> Đã có ảnh Cảnh {sc.scene_id} ({sc.camera_motion}), tái sử dụng...")
            sc.media_path = img_dest
            continue
        print(f"  -> Sinh ảnh Cảnh {sc.scene_id} ({sc.camera_motion})...")
        res_img = image_agent.generate_scene_image(
            prompt=sc.visual_prompt,
            output_path=img_dest,
            aspect_ratio="9:16",
            niche="satirical_psychology_animation",
            scene_id=sc.scene_id
        )
        sc.media_path = res_img or img_dest

    # 4. Tạo phụ đề Karaoke ASS tiếng Việt
    print("\n📝 [BƯỚC 3/4] Đang tạo phụ đề Karaoke ASS chuẩn tiếng Việt...")
    ass_path = str(work_dir / "subtitles.ass")
    SubtitleHelper.save_ass_file(enriched_scenes, ass_path, niche_id="satirical_psychology_animation")
    print(f"✅ Đã tạo phụ đề ASS: {ass_path}")

    # 5. Dựng video MP4 hoàn chỉnh với VideoAgent
    print("\n🎬 [BƯỚC 4/4] Đang render video MP4 hoàn chỉnh (Chuyển động gật đầu nod_loop + Ken Burns + BGM)...")
    video_agent = VideoAgent()
    final_output = str(work_dir / "Hoi_Chung_Nguoi_Tot_Pilot.mp4")

    render_job = RenderJobContract(
        job_id=999,
        title="Hội Chứng Người Tốt — Càng Ngoan Càng Bị Bắt Nạt",
        scenes=enriched_scenes,
        full_audio_path=full_audio_path,
        total_audio_duration_ms=int(total_duration * 1000),
        subtitle_path=ass_path,
        bgm_path="assets/bgm/deep_ambient.wav",
        bgm_volume=0.12,
        output_path=final_output,
        resolution_w=1080,
        resolution_h=1920,
        fps=30,
        niche="satirical_psychology_animation"
    )

    rendered_file = await video_agent.render(render_job)
    if rendered_file and Path(rendered_file).exists():
        v_info = probe_file(rendered_file)
        f_size = Path(rendered_file).stat().st_size
        f_dur = float(v_info.get("format", {}).get("duration", 0)) if v_info else 0
        print("\n" + "=" * 80)
        print(f"🎉 XUẤT XẮC! VIDEO PILOT ĐÃ RENDER THÀNH CÔNG!")
        print(f"📁 Đường dẫn: {rendered_file}")
        print(f"⏱️ Thời lượng: {f_dur:.1f}s | Dung lượng: {f_size / (1024*1024):.2f} MB")
        print("=" * 80)
    else:
        print("❌ Lỗi render video hoàn chỉnh!")

if __name__ == "__main__":
    asyncio.run(main())
