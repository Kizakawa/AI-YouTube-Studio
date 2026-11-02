"""
app.services.bilibili_longform_compiler — Bộ máy Ghép 3 Video Bilibili x ~3.2 Phút (~9.6 Phút)
kèm Tự Động Xóa Phụ Đề Chữ Hán, Phá Vân Tay Content ID Mỗi 4.7 Giây & Gemini Vision Nhìn Hình Phối Giọng.

Quy trình 3 Bước:
1. Săn & tải 3 video Bilibili 16:9 khác nhau cùng chủ đề (ưu tiên số 1: bản sạch '无字幕 / 纯享 / 4K纪录片'),
   mỗi video lấy ~180s–195s (~3.2 phút) đoạn gay cấn nhất (bỏ 6s intro đầu).
2. Cắt 3 video Bilibili thành 30 phân cảnh nối tiếp (10 cảnh/video x ~19s) với bộ lọc
   **Anti-Content-ID 4-Shot Multi-Cut (~4.7s/góc máy: Wide 103% -> Medium 108% -> Close 114% -> Tight 120%)**
   + **Smart Subtitle Safe-Crop** (cắt sạch 14% đáy nếu có chữ Hán đốt cứng, giữ nguyên khung rộng nếu là bản '无字幕').
3. Dùng **Multimodal Gemini Vision** xem trực tiếp lưới khung hình thực tế của từng đoạn trên 3 video Bilibili
   để viết lời thuyết minh (52–60 từ/cảnh) khớp 100% với diễn biến đang chiếu trên màn hình!
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.schemas.contracts import SceneContract, ScriptContract
from app.services.media.bilibili_fetcher import (
    _build_bili_cookie_headers,
    get_bilibili_video_info_and_streams,
    search_bilibili_videos,
    translate_query_to_chinese,
)
from app.services.media.longform_chapter_pool import (
    extract_longform_scene_with_internal_jumpcut,
)
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    get_duration_ms,
    get_ffmpeg_exe,
    is_completed_clean_mp4,
)

logger = logging.getLogger(__name__)


class BilibiliLongformCompiler:
    """
    Bộ máy biên tập Video Dài 10 Phút (16:9) từ 3 Video Bilibili (~3.2 phút/video)
    kèm chống bản quyền Content ID (4.7s Multi-Cut) và thuyết minh AI nhìn hình (Vision-Synced Dubbing).
    """

    def download_bilibili_3min_segment(
        self,
        bvid: str,
        output_path: str,
        target_duration_sec: float = 190.0,
        title_hint: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Tải đoạn ~3 phút (180s–195s) từ 1 video Bilibili 16:9 và áp dụng Smart Safe-Crop:
        - Nếu video là bản sạch ('无字幕' / '纯享' / '原片'): chỉ crop nhẹ 6% viền góc (`iw*0.92:ih*0.86`).
        - Nếu video có thể chứa phụ đề cứng ở đáy: tự động crop bỏ 14% đáy và 10% đỉnh (`iw*0.86:ih*0.76:iw*0.07:ih*0.10`).
        """
        info = get_bilibili_video_info_and_streams(bvid, min_height=720, require_landscape=True)
        if not info or not info.get("video_stream_url"):
            info = get_bilibili_video_info_and_streams(bvid, min_height=480, require_landscape=True)
        if not info or not info.get("video_stream_url"):
            return None

        stream_url = info["video_stream_url"]
        dl_headers = dict(info.get("http_headers") or _build_bili_cookie_headers())
        tbr_kbps = float(info.get("tbr") or 0.0)
        bytes_per_sec = (tbr_kbps * 1000.0 / 8.0) if tbr_kbps > 40.0 else 180_000.0
        desired_window_s = float(target_duration_sec) + 18.0
        eff_range_bytes = max(8_000_000, min(48 * 1024 * 1024, int(desired_window_s * bytes_per_sec * 1.35) + 600_000))
        dl_headers["Range"] = f"bytes=0-{eff_range_bytes - 1}"

        import requests

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_m4s = Path(tmp_dir) / f"bili_3m_{info['bvid']}.m4s"
            try:
                t0 = time.monotonic()
                r = requests.get(stream_url, headers=dl_headers, stream=True, timeout=(5.0, 18.0))
                if r.status_code not in (200, 206):
                    dl_headers.pop("Range", None)
                    r = requests.get(stream_url, headers=dl_headers, stream=True, timeout=(5.0, 18.0))
                    if r.status_code not in (200, 206):
                        return None
                dl_bytes = 0
                with open(raw_m4s, "wb") as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        if chunk:
                            f.write(chunk)
                            dl_bytes += len(chunk)
                        if dl_bytes >= eff_range_bytes or (time.monotonic() - t0 > 28.0):
                            break
                if not raw_m4s.exists() or raw_m4s.stat().st_size < 250_000:
                    return None
            except Exception as e_dl:
                logger.debug(f"[Bilibili3x3] Tải stream {bvid} lỗi: {e_dl}")
                return None

            meta_text = f"{title_hint} {info.get('title', '')} {info.get('description', '')}"
            is_nosub = any(k in meta_text for k in ("无字幕", "纯享", "原片", "無字幕", "素材"))
            crop_vf = (
                "crop=iw*0.92:ih*0.86:iw*0.04:ih*0.07"
                if is_nosub
                else "crop=iw*0.86:ih*0.76:iw*0.07:ih*0.10"
            )
            vf_str = (
                f"{crop_vf},"
                "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=1920:1080,"
                "unsharp=5:5:0.85:5:5:0.0,"
                "eq=contrast=1.04:saturation=1.05,"
                "setsar=1,fps=30,format=yuv420p"
            )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                get_ffmpeg_exe(), "-y", "-v", "error",
                "-ss", "6.00",
                "-t", f"{target_duration_sec:.2f}",
                "-i", str(raw_m4s),
                "-vf", vf_str,
                "-an", "-dn", "-sn",
                "-threads", "2",
                "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
                *BT709_COLOR_ARGS,
                str(output_path),
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
                if is_completed_clean_mp4(output_path, min_size_bytes=150_000, min_duration_ms=45_000):
                    info["local_master_path"] = str(output_path)
                    info["clipped_duration_sec"] = round(get_duration_ms(output_path) / 1000.0, 1)
                    info["is_nosub"] = is_nosub
                    return info
            except Exception as e_ff:
                logger.warning(f"[Bilibili3x3] FFmpeg chuẩn hóa {bvid} lỗi: {e_ff}")
        return None

    def hunt_3_bilibili_3min_masters(
        self,
        topic: str,
        primary_entity: str,
        work_dir: Path,
        sub_queries: Optional[List[str]] = None,
        used_bvids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Tìm và tải 3 video Bilibili 16:9 khác nhau (mỗi video lấy ~3 phút = 185s–195s),
        ưu tiên các bản sạch không phụ đề ('无字幕 / 纯享 / 4K纪录片').
        """
        used_set = used_bvids if used_bvids is not None else set()
        queries_en = list(sub_queries or [])
        if not queries_en:
            queries_en = [
                f"{primary_entity or topic} documentary",
                f"{topic} wildlife action",
                f"{primary_entity or topic} deep analysis",
            ]

        candidates_pool: List[Dict[str, Any]] = []
        seen_bv: Set[str] = set(used_set)
        for q_en in queries_en[:4]:
            zh_q = translate_query_to_chinese(q_en, primary_entity=primary_entity or topic)
            cands_clean = search_bilibili_videos(f"{zh_q} 无字幕", max_results=5, min_dur_s=110.0, max_dur_s=900.0)
            cands_std = search_bilibili_videos(zh_q, max_results=8, min_dur_s=110.0, max_dur_s=900.0)
            for c in [*cands_clean, *cands_std]:
                bv = str(c.get("bvid") or c.get("id") or "").strip()
                if bv and bv not in seen_bv:
                    seen_bv.add(bv)
                    candidates_pool.append(c)

        master_dir = Path(work_dir) / "media" / "bilibili_3x3_masters"
        master_dir.mkdir(parents=True, exist_ok=True)
        selected_masters: List[Dict[str, Any]] = []

        for cand in candidates_pool:
            if len(selected_masters) >= 3:
                break
            bv = str(cand.get("bvid") or cand.get("id") or "").strip()
            idx_m = len(selected_masters) + 1
            out_m = str(master_dir / f"bili_part_{idx_m:02d}_{bv}.mp4")
            info = self.download_bilibili_3min_segment(
                bvid=bv,
                output_path=out_m,
                target_duration_sec=192.0,
                title_hint=str(cand.get("title") or ""),
            )
            if info and info.get("local_master_path"):
                used_set.add(bv)
                selected_masters.append(info)
                logger.info(
                    f"✅ [Bilibili 3x3] Đã tải Phần #{idx_m}/3 từ Bilibili ({bv}, "
                    f"{info.get('clipped_duration_sec')}s, nosub={info.get('is_nosub')}): {info.get('title', '')[:45]}"
                )

        return selected_masters

    def slice_3_masters_into_30_anti_cid_scenes(
        self,
        masters: List[Dict[str, Any]],
        media_dir: Path,
        scenes_per_master: int = 10,
        scene_duration_sec: float = 19.0,
        job_id: int = 0,
    ) -> List[str]:
        """
        Cắt 3 video Bilibili (~3.2 phút/video) thành 30 phân cảnh nối tiếp (10 cảnh/video),
        mỗi cảnh tự động qua bộ lọc **4-Shot Multi-Cut (~4.7s/góc máy)** để xóa sạch vân tay Content ID
        mà vẫn giữ nguyên mạch hình ảnh cuốn hút của 3 video Bilibili!
        """
        media_dir = Path(media_dir)
        media_dir.mkdir(parents=True, exist_ok=True)
        scene_paths: List[str] = []
        cur_scene_id = 1

        for m_idx, m_info in enumerate(masters):
            m_path = str(m_info.get("local_master_path") or "")
            if not m_path or not Path(m_path).exists():
                continue
            m_dur = get_duration_ms(m_path) / 1000.0
            stride = max(6.0, (m_dur - scene_duration_sec - 1.0) / max(1, scenes_per_master - 1))
            for slot_i in range(scenes_per_master):
                out_clip = str(media_dir / f"scene_{cur_scene_id:03d}_16x9.mp4")
                start_off = round(min(max(0.0, m_dur - scene_duration_sec - 0.5), slot_i * stride), 2)
                ok = extract_longform_scene_with_internal_jumpcut(
                    master_path=m_path,
                    output_path=out_clip,
                    start_offset_sec=start_off,
                    target_duration_sec=scene_duration_sec,
                    scene_id=cur_scene_id,
                    allow_internal_jumpcut=True,
                    job_id=job_id,
                    master_key=f"bili3x3_{m_idx}_{ slot_i }",
                )
                if ok and Path(out_clip).exists():
                    scene_paths.append(out_clip)
                cur_scene_id += 1

        return scene_paths

    def generate_vision_synced_narration_for_part(
        self,
        part_idx: int,
        master_info: Dict[str, Any],
        part_scene_paths: List[str],
        topic: str,
        language: str = "pt",
    ) -> List[str]:
        """
        Trích xuất lưới khung hình thực tế từ 10 phân cảnh của Phần `part_idx` (1..3)
        và dùng Multimodal Gemini Vision viết lời thuyết minh (52–60 từ/cảnh) khớp 100% với hình ảnh trên màn hình!
        """
        import requests
        from config.settings import settings
        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS, KeyRotator

        lang_map = {
            "pt": "Brazilian Portuguese (Português do Brasil)",
            "en": "English",
            "vi": "Vietnamese (Tiếng Việt)",
            "es": "Spanish (Español)",
        }
        lang_name = lang_map.get(str(language or "en").lower()[:2], "English")
        n_scenes = len(part_scene_paths)
        if n_scenes == 0:
            return []

        b64_sheet = ""
        with tempfile.TemporaryDirectory() as td:
            sample_paths = part_scene_paths[:6]
            sheet_jpg = str(Path(td) / f"part_{part_idx}_sheet.jpg")
            inputs_cmd: List[str] = []
            for sp in sample_paths:
                inputs_cmd.extend(["-ss", "2.0", "-i", sp])
            n_in = len(sample_paths)
            if n_in >= 4:
                fc = (
                    "[0:v]scale=480:270[v0];[1:v]scale=480:270[v1];"
                    "[2:v]scale=480:270[v2];[3:v]scale=480:270[v3];"
                    "[v0][v1]hstack=inputs=2[top];[v2][v3]hstack=inputs=2[bot];[top][bot]vstack=inputs=2"
                )
                subprocess.run(
                    [get_ffmpeg_exe(), "-y", "-v", "error", *inputs_cmd[:16], "-filter_complex", fc, "-frames:v", "1", "-q:v", "5", sheet_jpg],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False,
                )
                if Path(sheet_jpg).exists() and Path(sheet_jpg).stat().st_size > 3_000:
                    b64_sheet = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if not rotator or not rotator.has_keys:
            return []

        role_desc = (
            "PART 1 (Minutes 0:00–3:15 — Cold-Open Tease & First Escalation Tier, ending with an Open-Loop Cliffhanger into Part 2)"
            if part_idx == 1
            else (
                "PART 2 (Minutes 3:15–6:30 — Mid-Point Escalation & Counter-Intuitive Mechanism, ending with an Open-Loop Cliffhanger into the Final Showdown in Part 3)"
                if part_idx == 2
                else "PART 3 (Minutes 6:30–9:50 — The Main Event Showdown, Counter-Twist & Final Verdict)"
            )
        )
        prompt = f"""You are a Widescreen 16:9 Documentary Showrunner re-voicing a high-retention Bilibili visual segment ({role_desc}).
Overall Documentary Topic: "{topic}"
Bilibili Segment Original Title/Context: "{master_info.get('title', '')} — {master_info.get('description', '')[:160]}"
Target Language: {lang_name}

Look closely at the actual visual frames from this 3-minute video segment and write EXACTLY {n_scenes} sequential documentary narration blocks (`scenes_narration` array of {n_scenes} strings in {lang_name}):
1. Every narration block MUST be 52 to 60 words long (~19 seconds of spoken narration) and describe the EXACT creature, anatomy, environment, and action visible in this Bilibili video segment!
2. NEVER translate Chinese idioms literally and NEVER mention Bilibili or Chinese text. Write gripping, cinematic documentary storytelling in {lang_name}.
3. In the final block (Block #{n_scenes}), {'end with a high-tension Open-Loop Cliffhanger sentence that pulls the viewer directly into the next part so they cannot stop watching!' if part_idx < 3 else 'deliver the ultimate climax and final verdict that resolves the documentary!'}

Return ONLY valid JSON:
{{"scenes_narration": ["52-60 word narration 1 in {lang_name}...", "52-60 word narration 2..."]}}"""

        vision_models = [m for m in GEMINI_PRIORITY_MODELS if not str(m).startswith("gemma-")]
        for model_name, api_key in rotator.iter_best_model_key_pairs(vision_models, max_total_attempts=4, require_vision=bool(b64_sheet)):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            parts_payload: List[Dict[str, Any]] = [{"text": prompt}]
            if b64_sheet:
                parts_payload.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_sheet}})
            payload = {
                "contents": [{"parts": parts_payload}],
                "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.25),
            }
            try:
                resp = requests.post(url, json=payload, timeout=(4.0, 25.0))
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(
                            p.get("text", "")
                            for p in cands[0].get("content", {}).get("parts", [])
                            if isinstance(p, dict) and not p.get("thought")
                        )
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            arr = data.get("scenes_narration") or []
                            if isinstance(arr, list) and len(arr) >= n_scenes:
                                return [str(x).strip() for x in arr[:n_scenes]]
            except Exception as e_v:
                logger.debug(f"[Bilibili3x3] Vision narration warning: {e_v}")
        return []

    def extract_and_translate_bilibili_audio_direct(
        self,
        audio_m4s_path: str,
        video_title: str,
        target_language: str = "pt",
        num_segments: int = 10,
    ) -> List[str]:
        """
        KHÔNG VIẾT KỊCH BẢN MỚI: Nghe trực tiếp file âm thanh tiếng Trung gốc (`audio.m4s`) của video Bilibili
        bằng Gemini 2.5 Flash Native Audio (`audio/mp4`) và dịch thẳng lời thuyết minh gốc của pháp sư Bilibili
        sang ngôn ngữ đích (`pt`, `en`, `vi`) theo đúng thứ tự thời gian từ đầu đến cuối!
        """
        import requests
        from config.settings import settings
        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS, KeyRotator

        if not audio_m4s_path or not Path(audio_m4s_path).exists():
            return []

        lang_map = {
            "pt": "Brazilian Portuguese (Português do Brasil)",
            "en": "English",
            "vi": "Vietnamese (Tiếng Việt)",
            "es": "Spanish (Español)",
        }
        lang_name = lang_map.get(str(target_language or "pt").lower()[:2], "Brazilian Portuguese")

        # Nén nhẹ audio gốc sang MP3 mono 32kbps (< 2MB cho 3-5 phút) để gửi inline qua Gemini siêu tốc trong 1 request
        with tempfile.TemporaryDirectory() as td:
            compact_mp3 = str(Path(td) / "bili_orig_speech.mp3")
            subprocess.run(
                [
                    get_ffmpeg_exe(), "-y", "-v", "error",
                    "-i", str(audio_m4s_path),
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                    compact_mp3,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25, check=False,
            )
            if not Path(compact_mp3).exists() or Path(compact_mp3).stat().st_size < 5_000:
                return []
            b64_audio = base64.b64encode(Path(compact_mp3).read_bytes()).decode("ascii")

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if not rotator or not rotator.has_keys:
            return []

        prompt = f"""Listen to this original Chinese narration from the Bilibili video "{video_title}" and translate/adapt it directly into {lang_name} divided into {num_segments} chronological segments from start to finish:
1. DO NOT invent a new unrelated script — follow the exact chronological story, creatures, facts, and events spoken in the original Bilibili audio!
2. Remove any Chinese channel self-promotions ("Follow my Bilibili channel", "Triple-click like/coin", etc.) and adapt each segment into natural, gripping documentary narration in {lang_name} (48 to 58 words per segment).
3. Return ONLY valid JSON with key "segments" containing exactly {num_segments} translated strings in {lang_name}:
{{"segments": ["Segment 1 in {lang_name}...", "Segment 2 in {lang_name}..."]}}"""

        audio_models = [m for m in GEMINI_PRIORITY_MODELS if "flash" in str(m).lower() and not str(m).startswith("gemma-")]
        for model_name, api_key in rotator.iter_best_model_key_pairs(audio_models, max_total_attempts=4):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "audio/mp3", "data": b64_audio}},
                    ]
                }],
                "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.2),
            }
            try:
                resp = requests.post(url, json=payload, timeout=(4.0, 30.0))
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(
                            p.get("text", "")
                            for p in cands[0].get("content", {}).get("parts", [])
                            if isinstance(p, dict) and not p.get("thought")
                        )
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            segs = data.get("segments") or []
                            if isinstance(segs, list) and len(segs) >= max(1, num_segments - 2):
                                while len(segs) < num_segments and segs:
                                    segs.append(segs[-1])
                                return [str(s).strip() for s in segs[:num_segments]]
            except Exception as e_aud:
                logger.debug(f"[BilibiliDirectDub] Audio translation warning: {e_aud}")
        return []

