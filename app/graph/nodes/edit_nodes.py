"""
app.graph.nodes.edit_nodes — Node Chỉnh sửa Video Vi mô (Granular Video Editing) trong LangGraph.

Cho phép can thiệp từng phân cảnh độc lập mà không phải render lại toàn bộ:
- replace_media: Đổi ảnh/video của 1 cảnh cụ thể
- edit_text: Sửa câu thoại của 1 cảnh (chỉ sinh lại audio cảnh đó)
- change_voice: Đổi giọng đọc nam/nữ toàn bộ video
- remix_bgm: Đổi nhạc nền trong 2 giây
"""
import asyncio
import json
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

    script_data = dict(state.get("script_data") or {})
    if not script_data and state.get("job_id"):
        from app.database.models import get_db, VideoJob
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == state["job_id"]).first()
            if j and j.scenes_json:
                try:
                    script_data = json.loads(j.scenes_json)
                except Exception:
                    pass

    logger.info(f"🛠️ [LangGraph Edit] Thực hiện chỉnh sửa vi mô: {action_type} (Scene {scene_id})")

    full_audio_path = state.get("full_audio_path", "")
    subtitle_path = state.get("subtitle_path", "")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)

    # ── Action 0: Đổi tiêu đề video ──
    if action_type == "edit_title":
        new_title = edit_req.get("new_title") or edit_req.get("new_text")
        if new_title:
            new_title = new_title.strip().strip('"\'')
            state["title"] = new_title
            if script_data:
                script_data["title"] = new_title
            if state.get("job_id"):
                from app.database.models import get_db, VideoJob
                with get_db() as db:
                    j = db.query(VideoJob).filter(VideoJob.id == state["job_id"]).first()
                    if j:
                        j.title = new_title
                        if j.scenes_json:
                            try:
                                sj = json.loads(j.scenes_json)
                                sj["title"] = new_title
                                j.scenes_json = json.dumps(sj, ensure_ascii=False)
                            except Exception:
                                pass
            logger.info(f"✅ [Edit] Đã cập nhật tiêu đề Video #{state.get('job_id')} thành: '{new_title}'")
            return {
                "script_data": script_data,
                "title": new_title,
                "current_step": "TITLE_EDITED",
                "progress_message": f"Đã cập nhật tiêu đề mới: {new_title}",
            }

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
            words_meta_path = str(audio_dir / f"scene_{scene_id:03d}.words.jsonl")
            safe_voice = voice_agent._resolve_safe_voice(clean_text, voice_agent.default_voice)
            tts_rate = "+7%" if safe_voice.startswith("vi-") else ("+9%" if safe_voice.startswith("pt-") else "+12%")

            try:
                communicate = edge_tts.Communicate(clean_text, safe_voice, rate=tts_rate, boundary="WordBoundary")
                await communicate.save(scene_audio_path, metadata_fname=words_meta_path)
            except Exception:
                communicate = edge_tts.Communicate(clean_text, safe_voice, rate=tts_rate)
                await communicate.save(scene_audio_path)

            new_dur = await asyncio.to_thread(voice_agent._get_audio_duration_ms, scene_audio_path)
            new_word_timings = voice_agent._parse_words_jsonl(words_meta_path)

            for sa in scenes_audio:
                if sa.get("scene_id") == scene_id:
                    sa["narration"] = clean_text
                    sa["duration_ms"] = new_dur
                    sa["audio_duration_ms"] = new_dur
                    sa["audio_path"] = scene_audio_path
                    sa["word_timings"] = new_word_timings if new_word_timings else None
                    break

            # Tạo lại toàn bộ audio concat & ASS Karaoke
            full_audio_path = str(work_dir / "full_voiceover.mp3")
            subtitle_path = str(work_dir / "subtitles.ass")
            scenes_objs = [
                SceneContract(
                    scene_id=s["scene_id"],
                    narration=s.get("narration", ""),
                    visual_keyword=s.get("visual_keyword", "documentary"),
                    audio_path=s.get("audio_path", ""),
                    audio_duration_ms=s.get("duration_ms") or s.get("audio_duration_ms", 3000),
                    word_timings=s.get("word_timings"),
                    estimated_seconds=3.0,
                )
                for s in scenes_audio
            ]
            total_audio_duration_ms = await voice_agent.concat_scene_audio(scenes_objs, full_audio_path)
            channel_style = state.get("channel_style") or "general"
            hook_banner_text = script_data.get("thumbnail_text") or script_data.get("hook") or script_data.get("title")
            SubtitleHelper.save_ass_file(scenes_objs, subtitle_path, niche_id=channel_style, hook_title=hook_banner_text)

    # ── Action 3: Đổi giọng đọc (toàn bộ video) ──
    elif action_type == "change_voice":
        new_voice = edit_req.get("new_voice_id")
        audio_dir = work_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Xây dựng danh sách SceneContract từ scenes_audio
        scenes_objs = [
            SceneContract(
                scene_id=s["scene_id"],
                narration=s.get("narration", ""),
                visual_keyword=s.get("visual_keyword", "documentary"),
                audio_path=s.get("audio_path", ""),
                audio_duration_ms=s.get("duration_ms") or s.get("audio_duration_ms", 3000),
                word_timings=s.get("word_timings"),
                estimated_seconds=3.0,
            )
            for s in scenes_audio
        ]

        voice_agent = VoiceAgent(default_voice=new_voice)
        enriched_scenes = await voice_agent.generate_per_scene_audio(
            scenes=scenes_objs,
            output_dir=str(audio_dir),
            voice=new_voice
        )

        scenes_audio = [
            {
                **s.model_dump(),
                "duration_ms": s.audio_duration_ms or 3000,
                "audio_path": s.audio_path
            }
            for s in enriched_scenes
        ]

        full_audio_path = str(work_dir / "full_voiceover.mp3")
        subtitle_path = str(work_dir / "subtitles.ass")
        total_audio_duration_ms = await voice_agent.concat_scene_audio(enriched_scenes, full_audio_path)
        channel_style = state.get("channel_style") or "general"
        hook_banner_text = script_data.get("thumbnail_text") or script_data.get("hook") or script_data.get("title")
        SubtitleHelper.save_ass_file(enriched_scenes, subtitle_path, niche_id=channel_style, hook_title=hook_banner_text)

    # ── Action 4: Đổi ngôn ngữ toàn bộ kịch bản & giọng lồng tiếng ──
    elif action_type == "change_language":
        from app.agents.content_agent import ContentAgent
        raw_lang = (edit_req.get("new_language") or edit_req.get("new_text") or "en").lower().strip()
        if "vi" in raw_lang:
            target_lang = "vi"
            lang_name = "Vietnamese"
        elif "pt" in raw_lang or "portug" in raw_lang or "brazil" in raw_lang:
            target_lang = "pt-BR"
            lang_name = "Brazilian Portuguese"
        else:
            target_lang = "en"
            lang_name = "English"

        ca = ContentAgent()
        try:
            current_script = ScriptContract(**script_data)
        except Exception:
            sc_list = script_data.get("scenes") or scenes_audio
            current_script = ScriptContract(
                title=script_data.get("title") or state.get("title") or "Video",
                hook=script_data.get("hook") or (sc_list[0].get("narration", "Hook") if sc_list else "Hook"),
                full_narration=script_data.get("full_narration") or " ".join(s.get("narration", "") for s in sc_list),
                scenes=[
                    SceneContract(
                        scene_id=idx + 1,
                        narration=s.get("narration", ""),
                        visual_keyword=s.get("visual_keyword", "nature"),
                    )
                    for idx, s in enumerate(sc_list)
                ],
                tags=script_data.get("tags") or ["shorts", "viral"],
                description=script_data.get("description"),
            )

        channel_style = state.get("channel_style") or "general"
        revised = ca.refine_script(
            current_script=current_script,
            feedback=f"Translate and fully rewrite the script into {lang_name}. Keep the viral hook, structure, and pacing.",
            channel_niche=channel_style,
            language=target_lang
        )
        script_data.update(revised.model_dump())

        # Đồng bộ hóa scenes_media theo số cảnh mới
        new_scenes_media = []
        for idx, sc in enumerate(revised.scenes):
            if idx < len(scenes_media):
                m_item = dict(scenes_media[idx])
                m_item["scene_id"] = sc.scene_id
                m_item["visual_keyword"] = sc.visual_keyword
                new_scenes_media.append(m_item)
            else:
                fallback_m = scenes_media[-1] if scenes_media else {"media_path": "", "media_type": "image"}
                new_scenes_media.append({
                    "scene_id": sc.scene_id,
                    "media_path": fallback_m.get("media_path", ""),
                    "media_type": fallback_m.get("media_type", "image"),
                    "visual_keyword": sc.visual_keyword,
                })
        scenes_media = new_scenes_media

        # Chọn giọng chuẩn theo ngôn ngữ mới
        req_voice = edit_req.get("new_voice_id")
        if not req_voice:
            if target_lang == "vi":
                req_voice = "vi-VN-NamMinhNeural"
            elif target_lang == "pt-BR":
                req_voice = "pt-BR-AntonioNeural"
            else:
                req_voice = "en-US-AndrewNeural"

        voice_agent = VoiceAgent(default_voice=req_voice)
        audio_dir = work_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Xóa các file audio cũ trong work_dir để đảm bảo tạo mới hoàn toàn
        for p in audio_dir.glob("scene_*.mp3"):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        for p in audio_dir.glob("scene_*.meta.json"):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

        enriched = await voice_agent.generate_per_scene_audio(
            scenes=revised.scenes,
            output_dir=str(audio_dir),
            voice=req_voice
        )
        scenes_audio = [
            {
                **s.model_dump(),
                "duration_ms": s.audio_duration_ms or 3000,
                "audio_path": s.audio_path
            }
            for s in enriched
        ]
        full_audio_path = str(work_dir / "full_voiceover.mp3")
        subtitle_path = str(work_dir / "subtitles.ass")
        total_audio_duration_ms = await voice_agent.concat_scene_audio(enriched, full_audio_path)
        channel_style = state.get("channel_style") or "general"
        hook_banner_text = script_data.get("thumbnail_text") or script_data.get("hook") or script_data.get("title")
        SubtitleHelper.save_ass_file(enriched, subtitle_path, niche_id=channel_style, hook_title=hook_banner_text)

    return {
        "script_data": script_data,
        "scenes_media": scenes_media,
        "scenes_audio": scenes_audio,
        "full_audio_path": full_audio_path,
        "subtitle_path": subtitle_path,
        "total_audio_duration_ms": total_audio_duration_ms,
        "current_step": "EDIT_APPLIED",
        "progress_message": f"Đã áp dụng chỉnh sửa vi mô '{action_type}'",
    }
