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

    @staticmethod
    def _measure_playable_pts_duration_sec(mp4_path: str, fallback_sec: float = 0.0) -> float:
        """
        Đo chính xác thời lượng khung hình thực tế đã tải trong file .m4s bằng `ffprobe packet=pts_time` (~0.18s)
        thay vì đoán qua `dl_bytes / peak_tbr` (vốn hay bị hụt 35%–45% do VBR).
        """
        try:
            p = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "packet=pts_time",
                    "-of", "csv=p=0",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            pts: List[float] = []
            for x in p.stdout.splitlines():
                tok = x.strip().rstrip(",")
                if tok and tok != "N/A":
                    try:
                        pts.append(float(tok))
                    except ValueError:
                        pass
            if pts:
                return round(max(pts), 2)
        except Exception:
            pass
        return round(float(fallback_sec), 2)

    def download_bilibili_3min_segment(
        self,
        bvid: str,
        output_path: str,
        target_duration_sec: float = 190.0,
        title_hint: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Tải đoạn ~2.5–3 phút từ 1 video Bilibili 16:9 chuẩn 1080p/4K (tbr >= 850 kbps)
        bằng cơ chế **Auto-Resuming 4MB Range Downloader**:
        - Tự động nối tiếp (`Range: bytes={dl_bytes}-...`) nếu CDN Bilibili ngắt kết nối giữa chừng (`IncompleteRead`).
        - Tự động từ chối phân mục Hoạt hình / Anime / Game (`tid` thuộc nhóm Douga/Game).
        """
        info = get_bilibili_video_info_and_streams(bvid, min_height=1080, require_landscape=True)
        if not info or not info.get("video_stream_url"):
            info = get_bilibili_video_info_and_streams(bvid, min_height=720, require_landscape=True)
        if not info or not info.get("video_stream_url"):
            return None

        # Từ chối ngay lập tức nếu video thuộc phân vùng Hoạt hình / Douga / Game / Nhạc / Vũ đạo trên Bilibili
        bili_tid = int(info.get("tid") or 0)
        if bili_tid in {1, 24, 25, 27, 47, 210, 86, 253, 32, 33, 51, 152, 153, 168, 195, 3, 4, 17, 171, 172, 65, 173, 129}:
            logger.debug(f"[Bilibili3x3] Bỏ qua {bvid} vì thuộc phân vùng phi tài liệu (tid={bili_tid})")
            return None

        tbr_kbps = float(info.get("tbr") or 0.0)
        v_h = int(info.get("height") or 0)
        min_req_tbr = 800.0 if v_h >= 1080 else 1100.0
        if v_h < 720 or (tbr_kbps > 0 and tbr_kbps < min_req_tbr):
            logger.debug(f"[Bilibili3x3] Bỏ qua {bvid} vì độ phân giải/bitrate thấp ({v_h}p, {tbr_kbps:.0f}kbps < {min_req_tbr:.0f}kbps)")
            return None

        stream_url = info["video_stream_url"]
        backup_urls = [str(u) for u in (info.get("video_backup_urls") or []) if u]
        url_pool: List[str] = []
        for u in [*[b for b in backup_urls if "bilivideo.com" in b], stream_url, *backup_urls]:
            if u and u not in url_pool:
                url_pool.append(u)

        dl_headers = dict(info.get("http_headers") or _build_bili_cookie_headers())
        bytes_per_sec = (tbr_kbps * 1000.0 / 8.0) if tbr_kbps > 40.0 else 220_000.0
        eff_target_s = min(105.0, float(target_duration_sec))
        desired_window_s = eff_target_s + 6.0
        eff_range_bytes = max(12_000_000, min(22 * 1024 * 1024, int(desired_window_s * bytes_per_sec * 1.02) + 400_000))

        from concurrent.futures import ThreadPoolExecutor
        import requests

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        raw_m4s = Path(output_path)
        try:
            t0 = time.monotonic()
            # Probe tổng dung lượng file từ Content-Range để không bao giờ tải lố EOF
            for p_url in url_pool:
                try:
                    rp = requests.get(p_url, headers=dict(dl_headers, Range="bytes=0-16383"), stream=True, timeout=(3.0, 5.0))
                    cr_hdr = str(rp.headers.get("Content-Range") or "")
                    rp.close()
                    if "/" in cr_hdr:
                        total_remote = int(cr_hdr.split("/")[-1].strip())
                        if total_remote > 0:
                            eff_range_bytes = min(eff_range_bytes, total_remote)
                            break
                except Exception:
                    pass

            chunk_span = 3 * 1024 * 1024  # Chia thành các lát 3MB tải SONG SONG 6 luồng (vượt rào cản bóp băng thông đơn luồng 220KB/s của Akamai CDN)
            ranges: List[Tuple[int, int]] = []
            cur_b = 0
            while cur_b < eff_range_bytes:
                end_b = min(eff_range_bytes - 1, cur_b + chunk_span - 1)
                ranges.append((cur_b, end_b))
                cur_b = end_b + 1

            def _dl_slice(idx_and_rng: Tuple[int, Tuple[int, int]]) -> bytes:
                s_idx, (b_start, b_end) = idx_and_rng
                expected_len = b_end - b_start + 1
                buf = bytearray()
                tries = 0
                sess = requests.Session()
                while len(buf) < expected_len and tries < 5 and (time.monotonic() - t0 < 55.0):
                    u_pick = url_pool[(s_idx + tries) % len(url_pool)]
                    cur_from = b_start + len(buf)
                    try:
                        r = sess.get(
                            u_pick,
                            headers=dict(dl_headers, Range=f"bytes={cur_from}-{b_end}"),
                            stream=True,
                            timeout=(3.5, 8.0),
                        )
                        if r.status_code not in (200, 206):
                            tries += 1
                            continue
                        for ch in r.iter_content(chunk_size=262144):
                            if ch:
                                buf.extend(ch)
                            if len(buf) >= expected_len or (time.monotonic() - t0 > 55.0):
                                break
                    except Exception:
                        tries += 1
                return bytes(buf[:expected_len])

            with ThreadPoolExecutor(max_workers=6) as slice_pool:
                slice_buffers = list(slice_pool.map(_dl_slice, list(enumerate(ranges))))

            dl_bytes = 0
            with open(raw_m4s, "wb") as f:
                for idx_s, s_buf in enumerate(slice_buffers):
                    exp_s = ranges[idx_s][1] - ranges[idx_s][0] + 1
                    f.write(s_buf)
                    dl_bytes += len(s_buf)
                    # Dừng ghi ngay nếu một phân đoạn giữa bị đứt để tránh hỏng cấu trúc hộp MP4/M4S
                    if len(s_buf) < exp_s:
                        break

            if not raw_m4s.exists() or raw_m4s.stat().st_size < 4_500_000:
                raw_m4s.unlink(missing_ok=True)
                return None
        except Exception as e_dl:
            logger.debug(f"[Bilibili3x3] Tải stream {bvid} lỗi: {e_dl}")
            raw_m4s.unlink(missing_ok=True)
            return None

        # Đo chính xác thời lượng PTS thực tế nằm trong số byte đã tải bằng ffprobe
        est_chunk_dur_s = max(25.0, (dl_bytes / max(25_000.0, bytes_per_sec)) * 0.92)
        avail_dur_s = self._measure_playable_pts_duration_sec(str(raw_m4s), fallback_sec=est_chunk_dur_s)
        if avail_dur_s < 65.0:
            raw_m4s.unlink(missing_ok=True)
            return None

        if not self._is_clean_cinematic_master(str(raw_m4s), playable_dur_s=avail_dur_s):
            logger.warning(f"🚫 [Bilibili3x3] Loại bỏ {bvid} vì phát hiện hoạt hình 2D / ảnh chụp trang sách nền trắng.")
            raw_m4s.unlink(missing_ok=True)
            return None

        meta_text = f"{title_hint} {info.get('title', '')} {info.get('description', '')}"
        is_nosub = any(k in meta_text for k in ("无字幕", "纯享", "原片", "無字幕", "素材"))
        info["local_master_path"] = str(raw_m4s)
        info["clipped_duration_sec"] = round(avail_dur_s, 1)
        info["is_nosub"] = is_nosub

        aud_url = info.get("audio_stream_url")
        orig_aud_out = str(raw_m4s.with_suffix(".orig_audio.m4s"))
        if aud_url:
            try:
                aud_hdrs = dict(info.get("http_headers") or _build_bili_cookie_headers())
                aud_hdrs["Range"] = "bytes=0-5500000"
                r_a = requests.get(aud_url, headers=aud_hdrs, stream=True, timeout=(4.0, 9.0))
                if r_a.status_code in (200, 206):
                    with open(orig_aud_out, "wb") as fa:
                        for ch_a in r_a.iter_content(chunk_size=262144):
                            if ch_a:
                                fa.write(ch_a)
                    if Path(orig_aud_out).exists() and Path(orig_aud_out).stat().st_size > 20_000:
                        info["orig_audio_path"] = orig_aud_out
            except Exception:
                pass
        return info

    def _is_clean_cinematic_master(self, mp4_path: str, playable_dur_s: float = 0.0) -> bool:
        """
        Kiểm tra bằng FFmpeg + PIL + NumPy (4 khung hình rải đều trong vùng đã tải) để loại bỏ triệt để:
        1. Video chụp màn hình bài báo / sách giáo khoa chia đôi màn hình trắng (`pure_white_ratio > 0.34`),
        2. Video hoạt hình 2D / Vector / PPT phẳng (`mean_flat_blocks > 0.38` hoặc `mean_zero_grad > 0.55`),
        nhưng KHÔNG loại nhầm cảnh băng tuyết Bắc Cực hoặc đáy biển sâu của BBC.
        """
        try:
            import io
            import numpy as np
            from PIL import Image

            dur_s = float(playable_dur_s) if playable_dur_s > 5.0 else self._measure_playable_pts_duration_sec(mp4_path, fallback_sec=75.0)
            if dur_s < 12.0:
                return False

            flat_ratios: List[float] = []
            zgrad_ratios: List[float] = []
            hf_residuals: List[float] = []
            lap_p90s: List[float] = []
            for frac in (0.18, 0.40, 0.62, 0.82):
                raw = subprocess.run(
                    [
                        get_ffmpeg_exe(),
                        "-v", "error",
                        "-ss", f"{max(6.0, dur_s * frac):.2f}",
                        "-i", str(mp4_path),
                        "-frames:v", "1",
                        "-f", "image2pipe",
                        "-vcodec", "png",
                        "-",
                    ],
                    capture_output=True,
                    timeout=8,
                    check=False,
                ).stdout
                if not raw:
                    continue
                im_full = Image.open(io.BytesIO(raw)).convert("L")
                w_n, h_n = im_full.size
                if w_n < 1280 or h_n < 720:
                    return False

                # 1) Kiểm tra hoạt hình 2D / sách giáo khoa nền trắng trên lưới chuẩn 320x180
                im_320 = im_full.resize((320, 180), Image.Resampling.BILINEAR)
                arr = np.array(im_320, dtype=np.float32)
                full_white = float(np.mean(arr >= 242))
                left_white = float(np.mean(arr[:, :145] >= 240))
                right_white = float(np.mean(arr[:, 175:] >= 240))
                if max(full_white, left_white, right_white) > 0.34:
                    return False

                blocks = arr[:176, :320].reshape(22, 8, 40, 8).transpose(0, 2, 1, 3)
                flat_ratios.append(float(np.mean(blocks.std(axis=(2, 3)) < 1.2)))
                dx = np.abs(np.diff(arr, axis=1))
                dy = np.abs(np.diff(arr, axis=0))
                zgrad_ratios.append(float((np.mean(dx < 0.4) + np.mean(dy < 0.4)) * 0.5))

                # 2) Đo độ sắc nét quang học THẬT trên khung hình gốc 1920x1080 (High-Frequency Residual & Laplacian P90)
                # Nếu video gốc là 360p/480p cũ kéo giãn lên vỏ 1080p ("Fake 1080p"), khi thu nhỏ 3x rồi phóng lại 3x sẽ gần như không mất chi tiết (HF < 2.15, P90 < 8.5)
                c_nat = im_full.crop((int(w_n * 0.12), int(h_n * 0.16), int(w_n * 0.88), int(h_n * 0.76)))
                a_nat = np.array(c_nat, dtype=np.float32)
                c_low = c_nat.resize((max(64, c_nat.width // 3), max(36, c_nat.height // 3)), Image.Resampling.BILINEAR).resize(
                    c_nat.size, Image.Resampling.BILINEAR
                )
                a_low = np.array(c_low, dtype=np.float32)
                hf_residuals.append(float(np.mean(np.abs(a_nat - a_low))))

                lap_nat = np.abs(
                    4.0 * a_nat[1:-1, 1:-1]
                    - a_nat[:-2, 1:-1]
                    - a_nat[2:, 1:-1]
                    - a_nat[1:-1, :-2]
                    - a_nat[1:-1, 2:]
                )
                lap_p90s.append(float(np.percentile(lap_nat, 90)))

            if flat_ratios and (float(np.mean(flat_ratios)) > 0.38 or float(np.mean(zgrad_ratios)) > 0.55):
                return False
            # Chặn triệt để video "Fake 1080p" (nguồn 360p/480p cũ kéo giãn lên vỏ 1080p có max_HF < 2.0 hoặc max_P90 < 8.5 trên mọi khung hình)
            if hf_residuals and (float(max(hf_residuals)) < 2.0 or float(max(lap_p90s)) < 8.5):
                logger.info(
                    f"🚫 [Anti-Fake-1080p] Loại bỏ {Path(mp4_path).name} vì nguồn gốc 480p mờ "
                    f"(Max_HF={float(max(hf_residuals)):.2f} < 2.0, Max_Lap_P90={float(max(lap_p90s)):.1f} < 8.5)"
                )
                return False
            return True
        except Exception:
            return True

    def hunt_3_bilibili_3min_masters(
        self,
        topic: str,
        primary_entity: str,
        work_dir: Path,
        sub_queries: Optional[List[str]] = None,
        used_bvids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Tìm và tải 3 video Bilibili 16:9 khác nhau (mỗi video lấy ~2.5–3 phút),
        ưu tiên các bản sạch không phụ đề ('无字幕 / 纯享 / 4K纪录片') và loại bỏ hoạt hình 2D / sách ảnh.
        """
        master_dir = Path(work_dir) / "media" / "bilibili_3x3_masters"
        master_dir.mkdir(parents=True, exist_ok=True)
        selected_masters: List[Dict[str, Any]] = []

        # Tái sử dụng ngay các file master Bilibili hợp lệ (>= 5MB, >= 65s thực tế) đã tải sẵn trên ổ cứng của job này
        used_set = used_bvids if used_bvids is not None else set()
        cached_mp4s = sorted(master_dir.glob("bili_part_*.mp4"))
        for cm in cached_mp4s:
            if len(selected_masters) >= 3:
                break
            if cm.stat().st_size >= 5_000_000:
                est_s = self._measure_playable_pts_duration_sec(str(cm), fallback_sec=(cm.stat().st_size / 250_000.0))
                if est_s >= 65.0 and self._is_clean_cinematic_master(str(cm), playable_dur_s=est_s):
                    bv_match = cm.stem.split("_")[-1]
                    aud_f = cm.with_suffix(".orig_audio.m4s")
                    used_set.add(bv_match)
                    selected_masters.append({
                        "bvid": bv_match,
                        "title": f"Bilibili 1080p Hot-Hit ({bv_match})",
                        "local_master_path": str(cm),
                        "clipped_duration_sec": round(est_s, 1),
                        "is_nosub": True,
                        "orig_audio_path": str(aud_f) if aud_f.exists() else None,
                    })
        if len(selected_masters) >= 3:
            logger.info(f"♻️ [Bilibili 3x3] Tái sử dụng 3 Master Bilibili đã tải sẵn trong {master_dir}")
            return selected_masters
        queries_en: List[str] = []
        if primary_entity:
            queries_en.append(str(primary_entity).strip())
        for sq in (sub_queries or []):
            if sq and str(sq).strip() not in queries_en:
                queries_en.append(str(sq).strip())
        if not queries_en:
            queries_en = [
                f"{primary_entity or topic}",
                f"{topic}",
                f"{primary_entity or topic} documentary 4K",
            ]

        bad_title_tokens = (
            "解说", "盘点", "辟谣", "评论", "网友", "一口气", "搞笑", "游戏",
            "动画", "漫画", "玩具", "手工", "测评", "说动物", "海洋公园", "表演", "水族馆",
            "绘本", "英文", "英语", "儿童", "少儿", "阅读", "朗读", "课件", "PPT",
            "模拟器", "我的世界", "挑战", "搞笑配音", "烧烤", "人生一串", "美食", "厨", "性侵",
        )
        candidates_pool: List[Dict[str, Any]] = []
        seen_bv: Set[str] = set(used_set)
        for q_en in queries_en[:4]:
            zh_q = translate_query_to_chinese(q_en, primary_entity=primary_entity or topic)
            # Tách danh từ chủ thể tiếng Trung gốc (ví dụ: '虎鲸', '美洲豹', '鳄鱼') để săn trọn vẹn Top Hot-Hit Triệu View
            base_zh = re.sub(r"\s*(纪录片|高清|4K|实拍|视频|捕猎|捕食|野生动物)\s*", " ", zh_q).strip() or zh_q
            core_zh = base_zh.split()[0] if base_zh.split() else base_zh
            cands_4k = search_bilibili_videos(f"{core_zh} 4K 纪录片", max_results=10, min_dur_s=95.0, max_dur_s=900.0, order="click")
            cands_bbc = search_bilibili_videos(f"{core_zh} BBC 纪录片", max_results=10, min_dur_s=95.0, max_dur_s=900.0, order="click")
            cands_hunt = search_bilibili_videos(f"{core_zh} 捕猎", max_results=10, min_dur_s=95.0, max_dur_s=900.0, order="click")
            cands_doc = search_bilibili_videos(f"{core_zh} 纪录片", max_results=10, min_dur_s=95.0, max_dur_s=900.0, order="click")
            cands_world = search_bilibili_videos(f"{core_zh} 动物世界", max_results=10, min_dur_s=95.0, max_dur_s=900.0, order="click")
            cands_full = search_bilibili_videos(f"{base_zh} 实拍", max_results=8, min_dur_s=95.0, max_dur_s=900.0, order="click")
            for c in [*cands_4k, *cands_bbc, *cands_hunt, *cands_doc, *cands_world, *cands_full]:
                bv = str(c.get("bvid") or c.get("id") or "").strip()
                t_str = str(c.get("title") or "")
                if bv and bv not in seen_bv and not any(bt in t_str for bt in bad_title_tokens):
                    seen_bv.add(bv)
                    candidates_pool.append(c)

        # Sắp xếp ứng viên kết hợp giữa Độ nét 4K/BBC/Siêu nét và Lượt xem Hot-Hit (view_count):
        # Ưu tiên các video Hot-Hit có dấu hiệu bản gốc 4K/BBC/1080p60 hoặc các video Triệu View (vốn sẽ được lọc tiếp qua cổng Anti-Fake-1080p HF_Residual >= 2.15)
        _uhd_markers = ("4k", "4K", "BBC", "bbc", "蓝光", "超清", "冰冻星球", "蓝色星球", "地球脉动", "完美星球", "无字幕", "纯享")
        candidates_pool.sort(
            key=lambda x: (
                1 if (any(m in str(x.get("title") or "") for m in _uhd_markers) and int(x.get("view_count") or 0) >= 8_000) else 0,
                int(x.get("view_count") or 0),
            ),
            reverse=True,
        )
        if candidates_pool:
            top_preview = ", ".join(
                f"{c.get('bvid')} ({int(c.get('view_count') or 0):,} views)"
                for c in candidates_pool[:6]
            )
            logger.info(f"🔥 [Bilibili 4K Hot-Hit] Top 6 ứng viên ưu tiên: {top_preview}")

        from concurrent.futures import ThreadPoolExecutor

        batch_size = 2
        for b_start in range(0, len(candidates_pool), batch_size):
            if len(selected_masters) >= 3:
                break
            batch = candidates_pool[b_start : b_start + batch_size]

            def _try_dl(item_pair: Tuple[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
                slot_num, cand_obj = item_pair
                bv_id = str(cand_obj.get("bvid") or cand_obj.get("id") or "").strip()
                out_m = str(master_dir / f"bili_part_{slot_num:02d}_{bv_id}.mp4")
                res_dl = self.download_bilibili_3min_segment(
                    bvid=bv_id,
                    output_path=out_m,
                    target_duration_sec=115.0,
                    title_hint=str(cand_obj.get("title") or ""),
                )
                if res_dl is not None:
                    res_dl["view_count"] = max(int(res_dl.get("view_count") or 0), int(cand_obj.get("view_count") or 0))
                return res_dl

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(_try_dl, [(b_start + i + 1, c) for i, c in enumerate(batch)]))
            for res_info in results:
                if len(selected_masters) >= 3:
                    break
                if res_info and res_info.get("local_master_path"):
                    bv = str(res_info.get("bvid") or "")
                    used_set.add(bv)
                    selected_masters.append(res_info)
                    logger.info(
                        f"✅ [Bilibili 3x3] Đã tải Phần #{len(selected_masters)}/3 từ Bilibili ({bv}, "
                        f"{int(res_info.get('view_count') or 0):,} views, "
                        f"{res_info.get('width')}x{res_info.get('height')} @ {res_info.get('tbr', 0):.0f}kbps, "
                        f"{res_info.get('clipped_duration_sec')}s): {res_info.get('title', '')[:45]}"
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

    @staticmethod
    def _extract_shifted_scene_ass(
        master_ass_path: str,
        out_scene_ass_path: str,
        start_sec: float,
        end_sec: float,
        overlay_badge: Optional[str] = None,
        auto_thumb_banner: Optional[str] = None,
        auto_thumb_local_sec: Optional[float] = None,
        scene_clip_dur_sec: Optional[float] = None,
    ) -> bool:
        """
        Cắt lát file phụ đề `.ass` tổng cho từng phân cảnh `[start_sec, end_sec]` và dịch gốc thời gian về `0.0s`
        (~0.4ms/cảnh) để đốt trực tiếp phụ đề ngay trong luồng cắt 6-Workers song song, giúp ghép cuối bằng `-c:v copy` (1.0 giây)!
        """
        if not master_ass_path or not Path(master_ass_path).exists():
            return False

        def _parse_ts(ts: str) -> float:
            h, m, s = ts.strip().split(":")
            return int(h) * 3600.0 + int(m) * 60.0 + float(s)

        def _fmt_ts(sec: float) -> str:
            sec = max(0.0, float(sec))
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = sec % 60.0
            return f"{h}:{m:02d}:{s:05.2f}"

        try:
            lines = Path(master_ass_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            out_lines: List[str] = []
            for ln in lines:
                if ln.startswith("Dialogue:"):
                    parts = ln.split(",", 9)
                    if len(parts) == 10:
                        if parts[3].strip() == "AutoThumbBanner" and auto_thumb_banner:
                            # Sẽ chèn theo tọa độ thời gian chính xác của clip cuối bên dưới
                            continue
                        st = _parse_ts(parts[1])
                        et = _parse_ts(parts[2])
                        if et > start_sec + 0.03 and st < end_sec - 0.03:
                            parts[1] = _fmt_ts(max(0.0, st - start_sec))
                            parts[2] = _fmt_ts(min(end_sec - start_sec, et - start_sec))
                            out_lines.append(",".join(parts))
                else:
                    out_lines.append(ln)
            if overlay_badge and not auto_thumb_banner:
                out_lines.append(
                    f"Dialogue: 1,0:00:00.20,0:00:04.20,PatternInterruptBadge,,0,0,0,,{{\\fad(200,250)}}{overlay_badge}"
                )
            if auto_thumb_banner and auto_thumb_local_sec is not None:
                max_d = float(scene_clip_dur_sec or (end_sec - start_sec) or 15.0)
                st_b = max(0.15, float(auto_thumb_local_sec) - 3.6)
                et_b = min(max_d - 0.15, float(auto_thumb_local_sec) + 3.6)
                if et_b <= st_b + 1.0:
                    st_b = 0.20
                    et_b = max(4.5, max_d - 0.20)
                out_lines.append(
                    f"Dialogue: 3,{_fmt_ts(st_b)},{_fmt_ts(et_b)},AutoThumbBanner,,0,0,0,,{{\\fad(150,150)}}{auto_thumb_banner}"
                )
            Path(out_scene_ass_path).write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

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
        target_min_duration_sec: float = 508.0,
    ) -> Dict[str, Any]:
        """
        Quy trình 1-Click Siêu Tốc (> 8 Phút 25 Giây mở khóa Quảng cáo giữa video Mid-Roll Ads, dựng xong trong ~25–35s):
        1. Tải song song 3 video Bilibili 16:9 + Tách luồng âm thanh tiếng Trung gốc.
        2. Dịch song song 3 luồng âm thanh bằng Gemini 2.5 Flash + Tạo giọng đọc AI (`VoiceAgent`) + Phụ đề `.ass`.
        3. Cắt SONG SONG 6-Workers từ 3 Master kèm **3-Shot Anti-CID (`Wide -> Medium -> Close`)**, **Cắt sạch phụ đề tiếng Trung**,
           và **Đốt trực tiếp phụ đề `.ass` vào từng cảnh** để bước ghép cuối dùng **Zero-Reencode `-f concat -c:v copy` (1.0 giây)**!
        """
        import asyncio
        from config.settings import settings
        from app.agents.voice_agent import VoiceAgent
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
        cached_scene_mp3s = sorted(audio_dir.glob("scene_*.mp3"))

        if Path(full_audio_path).exists() and len(cached_scene_mp3s) >= expected_total_scenes and Path(subtitle_path).exists():
            logger.info(f"♻️ [Bilibili 3x3] Tái sử dụng {len(cached_scene_mp3s)} file giọng đọc & phụ đề tại {audio_dir}")
            scenes_objs: List[SceneContract] = []
            for idx_s in range(expected_total_scenes):
                sid_idx = idx_s + 1
                a_path = str(cached_scene_mp3s[idx_s])
                a_ms = get_duration_ms(a_path) or 18000
                p_num = (idx_s // scenes_per_master) + 1
                badge = f"[ // PARTE {p_num}: ATO {(idx_s % scenes_per_master) + 1} // ]" if (idx_s % scenes_per_master == 0) else None
                scenes_objs.append(
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
                    )
                )
        else:
            async def _translate_part(idx_p: int, m_info: Dict[str, Any]) -> List[str]:
                n_segs = scenes_per_master
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
                        part_scene_paths=[str(m_info.get("local_master_path") or "")],
                        topic=topic,
                        language=language,
                    )
                while len(segs) < n_segs:
                    segs.append(
                        segs[-1]
                        if segs
                        else "Na natureza selvagem, cada segundo define quem domina o território e quem se torna presa imediata."
                    )
                return segs[:n_segs]

            part_narr_lists = await asyncio.gather(
                *[_translate_part(i + 1, m) for i, m in enumerate(masters)]
            )
            all_narrations: List[str] = [s for sub in part_narr_lists for s in sub]

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
            await asyncio.gather(
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

        # Tính toán thời lượng chuẩn từng cảnh + Khoảng thở B-Roll điện ảnh (broll_pause_s) để đảm bảo tổng thời lượng >= target_min_duration_sec (> 8m25s Mid-Roll Ads)
        total_sc_count = len(scenes_objs)
        spoken_durs: List[float] = []
        cum_starts: List[float] = []
        running_s = 0.0
        for sc in scenes_objs:
            a_ms = int(getattr(sc, "audio_duration_ms", 0) or get_duration_ms(str(sc.audio_path)) or 18000)
            s_dur = max(1.0, a_ms / 1000.0)
            cum_starts.append(running_s)
            spoken_durs.append(s_dur)
            running_s += s_dur

        broll_pause_s = (
            max(0.25, (float(target_min_duration_sec) - running_s) / max(1, total_sc_count))
            if running_s < float(target_min_duration_sec)
            else 0.25
        )

        # Tính chính xác trục thời gian sau khi cộng broll_pause_s để xác định đúng phân cảnh chứa mốc 25% - 50% - 75%
        from app.utils.subtitle_helper import SubtitleHelper as _SubH
        auto_thumb_text = _SubH.format_auto_thumb_banner_text(title or thumbnail_text)
        final_clip_durs: List[float] = [
            round((s_d + broll_pause_s) * 30.0) / 30.0 for s_d in spoken_durs
        ]
        final_cum_starts: List[float] = []
        f_run = 0.0
        for f_d in final_clip_durs:
            final_cum_starts.append(f_run)
            f_run += f_d
        total_final_dur_s = max(1.0, f_run)
        auto_thumb_scene_map: Dict[int, float] = {}
        for pct in (0.25, 0.50, 0.75):
            t_target = total_final_dur_s * pct
            for idx_cand, (f_st, f_d) in enumerate(zip(final_cum_starts, final_clip_durs)):
                if idx_cand > 0 and f_st <= t_target <= (f_st + f_d):
                    auto_thumb_scene_map[idx_cand] = round(t_target - f_st, 2)
                    break

        # Cắt SONG SONG 4-Workers (3-Shot Anti-CID + Cinema Bars + Đốt trực tiếp phụ đề .ass từng cảnh + Chuẩn hóa Audio AAC từng cảnh)
        sem = asyncio.Semaphore(4)

        def _slice_and_burn_scene_direct(
            m_path: str,
            out_clip: str,
            a_in_path: str,
            a_out_path: str,
            rel_ass_path: str,
            s0_off: float,
            s1_off: float,
            s2_off: float,
            target_dur: float,
            slot_span: float,
            scene_id: int = 1,
        ) -> None:
            # 5-Layer Anti-Content-ID & Anti-AI-Scan Engine:
            # 1. Biến thiên tốc độ ngẫu nhiên bất đối xứng từng góc máy (1.08x-1.17x / 0.87x-0.93x — không bao giờ để 1.00x gốc)
            # 2. Dịch tâm khung hình ngẫu nhiên (Asymmetric Pan/Crop Jitter) + Cắt sạch 10% đỉnh & 22% đáy
            # 3. Lật gương ngang thông minh (Selective hflip) phá vỡ 100% vector chuyển động Optical Flow
            # 4. Biến đổi ma trận màu & Gamma (eq=contrast/saturation/gamma) + Làm nét phần cứng AMD CAS=0.45
            # 5. Đóng khung Cinema 2.35:1 Widescreen (1920x820 -> pad 1920x1080, chỉ phóng 1.08x giữ nguyên 92% nét gốc)
            seed = int(job_id) * 31 + int(scene_id) * 17
            shot_d = round(target_dur / 3.0, 2)
            max_avail_src = max(8.5, slot_span)

            spd0_fast = 1.08 + ((seed % 9) * 0.01)
            spd1_slow = 0.88 + (((seed // 3) % 6) * 0.01)
            spd2_fast = 1.10 + (((seed // 7) % 8) * 0.01)

            src_d0 = round(min(max_avail_src, shot_d * spd0_fast), 2)
            src_d1 = round(min(max_avail_src, shot_d * spd1_slow), 2)
            src_d2 = round(min(max_avail_src, shot_d * spd2_fast), 2)

            pts0 = f"setpts={(shot_d + 0.12) / max(0.5, src_d0):.4f}*PTS,"
            pts1 = f"setpts={(shot_d + 0.12) / max(0.5, src_d1):.4f}*PTS,"
            pts2 = f"setpts={(shot_d + 0.15) / max(0.5, src_d2):.4f}*PTS,"

            x0 = round(0.03 + ((seed % 4) * 0.005), 3)
            y0 = round(0.10 + (((seed // 2) % 3) * 0.004), 3)
            x1 = round(0.05 + (((seed // 3) % 4) * 0.005), 3)
            y1 = round(0.11 + (((seed // 5) % 3) * 0.004), 3)
            x2 = round(0.06 + (((seed // 7) % 4) * 0.005), 3)
            y2 = round(0.11 + (((seed // 11) % 3) * 0.004), 3)

            flip1 = "hflip," if (seed % 2 == 0) else ""
            flip2 = "hflip," if (seed % 2 == 1) else ""

            vf0 = (
                f"{pts0}crop=iw*0.92:ih*0.68:iw*{x0}:ih*{y0},"
                "scale=1920:820:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:820,setsar=1"
            )
            vf1 = (
                f"{pts1}crop=iw*0.89:ih*0.66:iw*{x1}:ih*{y1},{flip1}"
                "scale=1920:820:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:820,setsar=1"
            )
            vf2 = (
                f"{pts2}crop=iw*0.86:ih*0.65:iw*{x2}:ih*{y2},{flip2}"
                "scale=1920:820:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:820,setsar=1"
            )
            ass_filter = f",ass={rel_ass_path}" if rel_ass_path and Path(rel_ass_path).exists() else ""
            post_cinema = (
                "unsharp=5:5:0.85:3:3:0.35,cas=0.55,eq=contrast=1.06:saturation=1.08:gamma=0.98,"
                "pad=1920:1080:0:110:black,"
                "drawbox=x=0:y=0:w=1920:h=142:color=black@1.0:t=fill,"
                f"drawbox=x=0:y=920:w=1920:h=160:color=black@1.0:t=fill{ass_filter},fps=30,format=yuv420p"
            )
            fc_3in = f"[0:v]{vf0}[v0];[1:v]{vf1}[v1];[2:v]{vf2}[v2];[v0][v1][v2]concat=n=3:v=1:a=0,{post_cinema}[vout]"
            cmd_v = [
                get_ffmpeg_exe(), "-y", "-v", "error",
                "-ss", f"{s0_off:.2f}", "-t", f"{src_d0:.2f}", "-i", m_path,
                "-ss", f"{s1_off:.2f}", "-t", f"{src_d1:.2f}", "-i", m_path,
                "-ss", f"{s2_off:.2f}", "-t", f"{src_d2:.2f}", "-i", m_path,
                "-filter_complex", fc_3in,
                "-map", "[vout]",
                "-t", f"{target_dur:.4f}",
                "-threads", "3",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-maxrate", "6500k", "-bufsize", "13000k",
                "-r", "30", "-pix_fmt", "yuv420p", "-video_track_timescale", "15360",
                *BT709_COLOR_ARGS,
                "-an", "-dn", "-sn",
                out_clip,
            ]
            subprocess.run(cmd_v, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
            cmd_a = [
                get_ffmpeg_exe(), "-y", "-v", "error",
                "-i", a_in_path,
                "-af", f"apad=whole_dur={target_dur:.4f}",
                "-t", f"{target_dur:.4f}",
                "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
                a_out_path,
            ]
            subprocess.run(cmd_a, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25, check=False)

        async def _slice_task(idx_s: int, sc: SceneContract) -> Tuple[SceneContract, str]:
            async with sem:
                a_path = getattr(sc, "audio_path", "") or full_audio_path
                spoken_s = spoken_durs[idx_s]
                c_start_s = cum_starts[idx_s]
                target_clip_dur = round((spoken_s + broll_pause_s) * 30.0) / 30.0

                scene_ass = work_dir / f"sub_s{sc.scene_id:03d}.ass"
                scene_auto_local_sec = auto_thumb_scene_map.get(idx_s)
                self._extract_shifted_scene_ass(
                    master_ass_path=subtitle_path,
                    out_scene_ass_path=str(scene_ass),
                    start_sec=c_start_s,
                    end_sec=c_start_s + spoken_s,
                    overlay_badge=getattr(sc, "overlay_badge", None),
                    auto_thumb_banner=auto_thumb_text if scene_auto_local_sec is not None else None,
                    auto_thumb_local_sec=scene_auto_local_sec,
                    scene_clip_dur_sec=target_clip_dur,
                )
                try:
                    rel_ass = scene_ass.relative_to(Path.cwd()).as_posix()
                except Exception:
                    rel_ass = scene_ass.as_posix()

                m_idx = min(len(masters) - 1, idx_s // scenes_per_master)
                slot_i = idx_s % scenes_per_master
                m_info = masters[m_idx]
                m_path = str(m_info.get("local_master_path") or "")
                m_dur = float(m_info.get("clipped_duration_sec") or 0.0) or self._measure_playable_pts_duration_sec(m_path, fallback_sec=90.0)
                safe_head = 5.0 if m_dur > 45.0 else 2.0
                usable_span = max(24.0, m_dur - safe_head - 11.5)
                n_slots = max(1, scenes_per_master)

                s0_off = round(safe_head + ((slot_i / n_slots) * usable_span), 2)
                s1_off = round(safe_head + ((((slot_i + 0.35) / n_slots) % 1.0) * usable_span), 2)
                s2_off = round(safe_head + ((((slot_i + 0.70) / n_slots) % 1.0) * usable_span), 2)
                slot_span = max(8.5, min(11.2, m_dur - max(s0_off, s1_off, s2_off) - 0.6))

                out_clip = str(media_dir / f"scene_{sc.scene_id:03d}_16x9.mp4")
                out_aud_aac = str(audio_dir / f"scene_{sc.scene_id:03d}_paced.m4a")

                await asyncio.to_thread(
                    _slice_and_burn_scene_direct,
                    m_path,
                    out_clip,
                    a_path,
                    out_aud_aac,
                    rel_ass,
                    s0_off,
                    s1_off,
                    s2_off,
                    target_clip_dur,
                    slot_span,
                    sc.scene_id,
                )
                sc.audio_path = a_path
                sc.audio_duration_ms = int(round(target_clip_dur * 1000))
                sc.media_path = out_clip
                sc.media_type = "video"
                return sc, out_aud_aac

        slice_pairs = list(
            await asyncio.gather(*[_slice_task(i, sc) for i, sc in enumerate(scenes_objs)])
        )
        enriched_scenes = [p[0] for p in slice_pairs]
        paced_aac_paths = [p[1] for p in slice_pairs]

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

        # Ghép nối Siêu Tốc Zero-Reencode (-f concat -c:v copy) trong ~3.0 giây!
        v_concat_txt = work_dir / "fast_v_concat.txt"
        a_concat_txt = work_dir / "fast_a_concat.txt"
        v_concat_txt.write_text(
            "\n".join(f"file '{Path(sc.media_path).resolve().as_posix()}'" for sc in enriched_scenes) + "\n",
            encoding="utf-8",
        )
        a_concat_txt.write_text(
            "\n".join(f"file '{Path(ap).resolve().as_posix()}'" for ap in paced_aac_paths) + "\n",
            encoding="utf-8",
        )
        voice_concat_m4a = str(work_dir / "voice_concat_full.m4a")
        final_mixed_m4a = str(work_dir / "final_mixed_audio.m4a")

        subprocess.run(
            [
                get_ffmpeg_exe(), "-y", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", str(a_concat_txt),
                "-c:a", "copy", voice_concat_m4a,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
        )

        bgm_candidates = sorted(Path("assets/bgm").glob("*.mp3")) if Path("assets/bgm").exists() else []
        if not bgm_candidates and Path("assets/music").exists():
            bgm_candidates = sorted(Path("assets/music").glob("*.mp3"))
        bgm_path = str(bgm_candidates[job_id % len(bgm_candidates)]) if bgm_candidates else None

        if bgm_path and Path(bgm_path).exists():
            subprocess.run(
                [
                    get_ffmpeg_exe(), "-y", "-v", "error",
                    "-i", voice_concat_m4a,
                    "-stream_loop", "-1", "-i", bgm_path,
                    "-filter_complex", "[0:a]volume=1.08[vo];[1:a]volume=0.14[bg];[vo][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                    "-map", "[aout]",
                    "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
                    final_mixed_m4a,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
        else:
            final_mixed_m4a = voice_concat_m4a

        subprocess.run(
            [
                get_ffmpeg_exe(), "-y", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", str(v_concat_txt),
                "-i", final_mixed_m4a if Path(final_mixed_m4a).exists() else voice_concat_m4a,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart",
                final_video_path,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )

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


