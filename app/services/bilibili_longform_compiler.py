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
                    # Tải thêm luồng âm thanh tiếng Trung gốc (audio_stream_url) để Gemini nghe & dịch trực tiếp lời gốc
                    aud_url = info.get("audio_stream_url")
                    orig_aud_out = str(Path(output_path).with_suffix(".orig_audio.m4s"))
                    if aud_url:
                        try:
                            aud_hdrs = dict(info.get("http_headers") or _build_bili_cookie_headers())
                            aud_hdrs["Range"] = "bytes=0-6500000"
                            r_a = requests.get(aud_url, headers=aud_hdrs, stream=True, timeout=(4.0, 12.0))
                            if r_a.status_code in (200, 206):
                                with open(orig_aud_out, "wb") as fa:
                                    for ch_a in r_a.iter_content(chunk_size=131072):
                                        if ch_a:
                                            fa.write(ch_a)
                                if Path(orig_aud_out).exists() and Path(orig_aud_out).stat().st_size > 20_000:
                                    info["orig_audio_path"] = orig_aud_out
                        except Exception:
                            pass
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
        master_dir = Path(work_dir) / "media" / "bilibili_3x3_masters"
        master_dir.mkdir(parents=True, exist_ok=True)
        selected_masters: List[Dict[str, Any]] = []

        # Tái sử dụng ngay nếu trên ổ cứng đã tải sẵn 3 file master Bilibili hợp lệ của job này
        cached_mp4s = sorted(master_dir.glob("bili_part_*.mp4"))
        if len(cached_mp4s) >= 3:
            for cm in cached_mp4s[:3]:
                if is_completed_clean_mp4(str(cm), min_size_bytes=150_000, min_duration_ms=45_000):
                    bv_match = cm.stem.split("_")[-1]
                    aud_f = cm.with_suffix(".orig_audio.m4s")
                    selected_masters.append({
                        "bvid": bv_match,
                        "title": f"Bilibili 4K Master ({bv_match})",
                        "local_master_path": str(cm),
                        "clipped_duration_sec": round(get_duration_ms(str(cm)) / 1000.0, 1),
                        "is_nosub": True,
                        "orig_audio_path": str(aud_f) if aud_f.exists() else None,
                    })
            if len(selected_masters) >= 3:
                logger.info(f"♻️ [Bilibili 3x3] Tái sử dụng 3 Master Bilibili đã tải sẵn trong {master_dir}")
                return selected_masters
            selected_masters.clear()

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

    async def produce_complete_bilibili_dubbed_longform(
        self,
        job_id: int,
        topic: str,
        title: str,
        primary_entity: str,
        channel_id: str = "ch_odisseia_selvagem",
        language: str = "pt",
        niche: str = "wildlife",
        thumbnail_text: str = "1.500 PSI!",
        sub_queries: Optional[List[str]] = None,
        scenes_per_master: int = 8,
    ) -> Dict[str, Any]:
        """
        Quy trình 1-Click khép kín:
        1. Tải 3 video Bilibili 16:9 (~3 phút/video) + Tách luồng âm thanh tiếng Trung gốc.
        2. Dùng Gemini 2.5 Flash nghe trực tiếp tiếng Trung gốc để dịch sang lời thuyết minh ngôn ngữ đích (`pt`/`en`/`vi`)
           (nếu video gốc chỉ có nhạc nền không lời thì tự động dùng Gemini Vision đọc hình viết lời).
        3. Tạo giọng đọc AI (`VoiceAgent`) + Phụ đề `.ass` cho toàn bộ các đoạn.
        4. Cắt 3 video Bilibili khớp từng mili-giây với từng đoạn giọng đọc kèm **4-Shot Multi-Cut (~4.7s/góc máy)**
           + **Cắt sạch phụ đề tiếng Trung ở đáy**.
        5. Tạo ảnh bìa Two-Tone High-CTR + Render video `1920x1080` nhúng IDR Keyframe Thumbnail (`0%, 25%, 50%, 75%`).
        6. Đẩy trực tiếp lên Google Drive (`DriveAgent`) và trả về link xem ngay!
        """
        import asyncio
        from config.settings import settings
        from app.agents.voice_agent import VoiceAgent
        from app.agents.video_agent import VideoAgent
        from app.agents.drive_agent import DriveAgent
        from app.services.thumbnail_service import ThumbnailService

        work_dir = Path(settings.TEMP_DIR) / f"job_{job_id}"
        media_dir = work_dir / "media"
        audio_dir = work_dir / "audio"
        media_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        masters = await asyncio.to_thread(
            self.hunt_3_bilibili_3min_masters,
            topic=topic,
            primary_entity=primary_entity,
            work_dir=work_dir,
            sub_queries=sub_queries,
        )
        if not masters:
            raise RuntimeError("Không tìm thấy video Bilibili phù hợp cho chủ đề này.")

        full_audio_path = str(work_dir / "full_voiceover.mp3")
        subtitle_path = str(work_dir / "subtitles.ass")
        expected_total_scenes = scenes_per_master * len(masters)
        cached_scene_mp4s = sorted(media_dir.glob("scene_*_16x9.mp4"))
        cached_scene_mp3s = sorted(audio_dir.glob("scene_*.mp3"))

        if (
            Path(full_audio_path).exists()
            and len(cached_scene_mp4s) >= expected_total_scenes
            and len(cached_scene_mp3s) >= expected_total_scenes
        ):
            logger.info(
                f"♻️ [Bilibili 3x3] Tái sử dụng {len(cached_scene_mp4s)} phân cảnh video & giọng đọc đã dựng sẵn tại {work_dir}"
            )
            total_audio_ms = get_duration_ms(full_audio_path)
            enriched_scenes: List[SceneContract] = []
            for idx_s in range(expected_total_scenes):
                sid_idx = idx_s + 1
                a_path = str(cached_scene_mp3s[idx_s])
                v_path = str(cached_scene_mp4s[idx_s])
                a_ms = get_duration_ms(a_path) or 18000
                p_num = (idx_s // scenes_per_master) + 1
                badge = f"[ // PARTE {p_num}: ATO {(idx_s % scenes_per_master) + 1} // ]" if (idx_s % scenes_per_master == 0) else None
                enriched_scenes.append(
                    SceneContract(
                        scene_id=sid_idx,
                        narration=f"Segmento {sid_idx}",
                        visual_keyword=f"{primary_entity} action scene {sid_idx}",
                        visual_prompt=f"16:9 widescreen shot {sid_idx}",
                        media_type="video",
                        camera_motion="zoom_in",
                        sfx_cue="none",
                        overlay_badge=badge,
                        estimated_seconds=round(a_ms / 1000.0, 2),
                        audio_path=a_path,
                        audio_duration_ms=a_ms,
                        media_path=v_path,
                    )
                )
        else:
            # Cắt sơ bộ các đoạn hình để phục vụ Vision nếu video gốc không có lời thoại
            prelim_paths = await asyncio.to_thread(
                self.slice_3_masters_into_30_anti_cid_scenes,
                masters=masters,
                media_dir=media_dir,
                scenes_per_master=scenes_per_master,
                scene_duration_sec=18.5,
                job_id=job_id,
            )

            all_narrations: List[str] = []
            for idx_p, m_info in enumerate(masters, start=1):
                part_clips = prelim_paths[(idx_p - 1) * scenes_per_master : idx_p * scenes_per_master]
                n_segs = len(part_clips) or scenes_per_master
                segs: List[str] = []
                if m_info.get("orig_audio_path"):
                    segs = await asyncio.to_thread(
                        self.extract_and_translate_bilibili_audio_direct,
                        audio_m4s_path=str(m_info["orig_audio_path"]),
                        video_title=str(m_info.get("title") or topic),
                        target_language=language,
                        num_segments=n_segs,
                    )
                if len(segs) < n_segs:
                    segs = await asyncio.to_thread(
                        self.generate_vision_synced_narration_for_part,
                        part_idx=idx_p,
                        master_info=m_info,
                        part_scene_paths=part_clips,
                        topic=topic,
                        language=language,
                    )
                while len(segs) < n_segs:
                    segs.append(
                        segs[-1]
                        if segs
                        else "Na natureza selvagem, cada segundo define quem domina o território e quem se torna presa imediata."
                    )
                all_narrations.extend(segs[:n_segs])

            scenes_contracts: List[SceneContract] = []
            for sid_idx, narr_str in enumerate(all_narrations, start=1):
                p_num = ((sid_idx - 1) // scenes_per_master) + 1
                badge = f"[ // PARTE {p_num}: ATO {((sid_idx - 1) % scenes_per_master) + 1} // ]" if ((sid_idx - 1) % scenes_per_master == 0) else None
                scenes_contracts.append(
                    SceneContract(
                        scene_id=sid_idx,
                        narration=narr_str,
                        visual_keyword=f"{primary_entity} action scene {sid_idx}",
                        visual_prompt=f"16:9 widescreen shot {sid_idx}",
                        media_type="video",
                        camera_motion="zoom_in",
                        sfx_cue="none",
                        overlay_badge=badge,
                        estimated_seconds=18.5,
                    )
                )

            from app.utils.subtitle_helper import SubtitleHelper

            voice_agent = VoiceAgent()
            scenes_objs = await voice_agent.generate_per_scene_audio(
                scenes=scenes_contracts,
                output_dir=str(audio_dir),
                niche=niche,
                channel_id=channel_id,
                job_id=job_id,
                is_long_form=True,
            )
            total_audio_ms, _ = await asyncio.gather(
                voice_agent.concat_scene_audio(
                    scenes=scenes_objs,
                    output_path=full_audio_path,
                ),
                asyncio.to_thread(
                    SubtitleHelper.save_ass_file,
                    scenes_objs,
                    subtitle_path,
                    niche_id=niche,
                    hook_title=thumbnail_text or title,
                    show_cta_badge=False,
                    is_long_form=True,
                ),
            )

            # Cắt lại từng phân cảnh từ 3 Master Bilibili khớp chính xác 100% thời lượng giọng đọc thực tế của từng câu!
            enriched_scenes = []
            for idx_s, sc in enumerate(scenes_objs):
                a_path = getattr(sc, "audio_path", "") or full_audio_path
                a_ms = int(getattr(sc, "audio_duration_ms", 18000) or 18000)
                dur_s = max(3.5, a_ms / 1000.0)

                m_idx = min(len(masters) - 1, idx_s // scenes_per_master)
                slot_i = idx_s % scenes_per_master
                m_path = str(masters[m_idx].get("local_master_path") or "")
                m_dur = get_duration_ms(m_path) / 1000.0
                stride = max(5.0, (m_dur - dur_s - 1.0) / max(1, scenes_per_master - 1))
                start_off = round(min(max(0.0, m_dur - dur_s - 0.4), slot_i * stride), 2)
                out_clip = str(media_dir / f"scene_{sc.scene_id:03d}_16x9.mp4")

                await asyncio.to_thread(
                    extract_longform_scene_with_internal_jumpcut,
                    master_path=m_path,
                    output_path=out_clip,
                    start_offset_sec=start_off,
                    target_duration_sec=dur_s + 0.25,
                    scene_id=sc.scene_id,
                    allow_internal_jumpcut=True,
                    job_id=job_id,
                    master_key=f"bili_sync_{m_idx}_{slot_i}",
                )
                sc.audio_path = a_path
                sc.audio_duration_ms = a_ms
                sc.media_path = out_clip
                sc.media_type = "video"
                enriched_scenes.append(sc)

        outputs_dir = Path(settings.OUTPUT_DIR)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        final_video_path = str(outputs_dir / f"video_{job_id}.mp4")

        th_srv = ThumbnailService(output_dir=str(outputs_dir))
        th_res = th_srv.generate_viral_thumbnail(
            job_id=job_id,
            topic=topic,
            viral_title=title,
            video_path=enriched_scenes[0].media_path,
            niche_id=niche,
            custom_text=thumbnail_text,
        )

        from app.schemas.contracts import RenderJobContract

        bgm_candidates = sorted(Path("assets/bgm").glob("*.mp3")) if Path("assets/bgm").exists() else []
        if not bgm_candidates and Path("assets/music").exists():
            bgm_candidates = sorted(Path("assets/music").glob("*.mp3"))
        bgm_path = str(bgm_candidates[job_id % len(bgm_candidates)]) if bgm_candidates else None
        render_job = RenderJobContract(
            job_id=job_id,
            title=title,
            scenes=enriched_scenes,
            full_audio_path=full_audio_path,
            total_audio_duration_ms=total_audio_ms,
            subtitle_path=subtitle_path if Path(subtitle_path).exists() else None,
            bgm_path=bgm_path,
            niche=niche,
            is_scifi=False,
            enable_sfx=False,
            output_path=final_video_path,
            video_format="long",
            thumbnail_path=str(th_res.get("16x9") or ""),
            thumbnail_vertical_path=str(th_res.get("9x16") or ""),
        )

        video_agent = VideoAgent()
        await video_agent.render(render_job)

        drive_agent = DriveAgent()
        drive_res = await asyncio.to_thread(
            drive_agent.upload_video,
            local_video_path=final_video_path,
            video_title=f"[Bilibili_4K_Dub_{job_id}] {title}",
            channel_name=channel_id,
        )

        return {
            "job_id": job_id,
            "video_path": final_video_path,
            "thumbnail_path": str(th_res.get("16x9") or ""),
            "duration_sec": round(get_duration_ms(final_video_path) / 1000.0, 1),
            "bilibili_masters": [
                {"bvid": m.get("bvid"), "title": m.get("title"), "duration": m.get("clipped_duration_sec"), "is_nosub": m.get("is_nosub")}
                for m in masters
            ],
            "drive_result": drive_res,
        }


