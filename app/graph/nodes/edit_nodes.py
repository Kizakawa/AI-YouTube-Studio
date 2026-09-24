"""
app.graph.nodes.edit_nodes — Node Chỉnh sửa Video Vi mô (Granular Video Editing) trong LangGraph.

Cho phép can thiệp từng phân cảnh độc lập mà không phải render lại toàn bộ:
- replace_media: Đổi ảnh/video của 1 cảnh cụ thể
- edit_text: Sửa câu thoại của 1 cảnh (chỉ sinh lại audio cảnh đó)
- change_voice: Đổi giọng đọc nam/nữ toàn bộ video
- remix_bgm: Đổi nhạc nền trong 2 giây
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.voice_agent import VoiceAgent
from app.agents.media_agent import MediaAgent
from app.agents.image_agent import ImageAgent
from app.utils.subtitle_helper import SubtitleHelper
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_execute_edit(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node thực thi lệnh chỉnh sửa vi mô từ người dùng hoặc hệ thống."""
    edit_req = state.get("edit_request", {})
    action_type = edit_req.get("action_type")
    scene_id = edit_req.get("scene_id")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    scenes_media = list(state.get("scenes_media", []))
    scenes_audio = list(state.get("scenes_audio", []))
    primary_entity = state.get("primary_entity")

    logger.info(f"🛠️ [LangGraph Edit] Thực hiện chỉnh sửa vi mô: {action_type} (Scene {scene_id})")

    full_audio_path = state.get("full_audio_path", "")
    subtitle_path = state.get("subtitle_path", "")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)

    # ── Action 1: Thay thế media của 1 cảnh cụ thể ──
    if action_type == "replace_media" and scene_id:
        new_query = edit_req.get("new_media_query")
        new_path = edit_req.get("new_media_path")
        media_dir = work_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        target_media_path = ""
        media_type = "image"

        if new_path and Path(new_path).exists():
            target_media_path = new_path
            media_type = "video" if new_path.lower().endswith((".mp4", ".mov")) else "image"
        elif new_query:
            # Tìm ảnh từ Wikimedia hoặc AI
            media_agent = MediaAgent()
            image_agent = ImageAgent()
            
            # Ưu tiên tìm ảnh thực tế nếu có nhân vật
            photos = await asyncio.to_thread(media_agent.search_web_photos, new_query, 2)
            if photos:
                url = photos[0].get("url")
                ext = ".jpg" if "jpg" in url.lower() else ".png"
                out_p = str(media_dir / f"scene_{scene_id:03d}_edited{ext}")
                await asyncio.to_thread(media_agent.download_file, url, out_p)
                if Path(out_p).exists() and Path(out_p).stat().st_size > 3000:
                    target_media_path = out_p
            
            if not target_media_path:
                # Sinh ảnh AI
                ai_out = str(media_dir / f"scene_{scene_id:03d}_edited_ai.jpg")
                res = await asyncio.to_thread(image_agent.generate_scene_image, new_query, ai_out)
                if res and Path(res).exists():
                    target_media_path = res

        if target_media_path:
            for sm in scenes_media:
                if sm.get("scene_id") == scene_id:
                    sm["media_path"] = target_media_path
                    sm["media_type"] = media_type
                    logger.info(f"✅ Đã cập nhật media cảnh {scene_id} thành: {target_media_path}")
                    break

    # ── Action 2: Sửa lời thoại của 1 cảnh ──
    elif action_type == "edit_text" and scene_id:
        new_text = edit_req.get("new_text", "")
        if new_text:
            import edge_tts
            voice_agent = VoiceAgent()
            audio_dir = work_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            clean_text = new_text.strip()
            scene_audio_path = str(audio_dir / f"scene_{scene_id:03d}.mp3")
            
            communicate = edge_tts.Communicate(clean_text, voice_agent.default_voice)
            await communicate.save(scene_audio_path)
            new_dur = voice_agent._get_audio_duration_ms(scene_audio_path)

            for sa in scenes_audio:
                if sa.get("scene_id") == scene_id:
                    sa["narration"] = clean_text
                    sa["duration_ms"] = new_dur
                    sa["audio_path"] = scene_audio_path
                    break

            # Tạo lại toàn bộ audio concat & SRT
            full_audio_path = str(work_dir / "full_voiceover.mp3")
            subtitle_path = str(work_dir / "subtitles.srt")
            scenes_objs = [
                SceneContract(
                    scene_id=s["scene_id"],
                    narration=s.get("narration", ""),
                    visual_keyword=s.get("visual_keyword", "documentary"),
                    audio_path=s.get("audio_path", ""),
                    audio_duration_ms=s.get("duration_ms") or s.get("audio_duration_ms", 3000),
                    estimated_seconds=3.0,
                )
                for s in scenes_audio
            ]
            total_audio_duration_ms = await voice_agent.concat_scene_audio(scenes_objs, full_audio_path)
            SubtitleHelper.save_srt_file(scenes_objs, subtitle_path)

    # ── Action 3: Đổi giọng đọc (toàn bộ video) ──
    elif action_type == "change_voice":
        import edge_tts
        new_voice = edit_req.get("new_voice_id", "vi-VN-HoaiMyNeural")
        voice_agent = VoiceAgent(default_voice=new_voice)
        audio_dir = work_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        for sa in scenes_audio:
            clean_text = sa.get("narration", "").strip()
            p = str(audio_dir / f"scene_{sa['scene_id']:03d}.mp3")
            communicate = edge_tts.Communicate(clean_text, new_voice)
            await communicate.save(p)
            d = voice_agent._get_audio_duration_ms(p)
            sa["duration_ms"] = d
            sa["audio_path"] = p

        full_audio_path = str(work_dir / "full_voiceover.mp3")
        subtitle_path = str(work_dir / "subtitles.srt")
        scenes_objs = [
            SceneContract(
                scene_id=s["scene_id"],
                narration=s.get("narration", ""),
                visual_keyword=s.get("visual_keyword", "documentary"),
                audio_path=s.get("audio_path", ""),
                audio_duration_ms=s.get("duration_ms") or s.get("audio_duration_ms", 3000),
                estimated_seconds=3.0,
            )
            for s in scenes_audio
        ]
        total_audio_duration_ms = await voice_agent.concat_scene_audio(scenes_objs, full_audio_path)
        SubtitleHelper.save_srt_file(scenes_objs, subtitle_path)

    return {
        "scenes_media": scenes_media,
        "scenes_audio": scenes_audio,
        "full_audio_path": full_audio_path,
        "subtitle_path": subtitle_path,
        "total_audio_duration_ms": total_audio_duration_ms,
        "current_step": "EDIT_APPLIED",
        "progress_message": f"Đã áp dụng chỉnh sửa vi mô '{action_type}'",
    }
