"""
app.services.thumbnail_service — Dịch Vụ Thiết Kế Ảnh Bìa (Thumbnail) Siêu Thu Hút CTR ("Thị Trường 1 Giây").

Nguyên lý thiết kế chuẩn YouTube Masterclass (12:15 - 17:55):
1. Thị trường 1 giây: Người xem lướt qua cực nhanh trên mobile; ảnh bìa phải truyền tải thông điệp và gợi tò mò trong < 1s.
2. Chữ cực to (Big Bold Typography): Dùng font siêu đậm (Arial Black / Impact), cỡ chữ 75px - 110px.
3. Độ tương phản tối đa (High Contrast): Chữ vàng Neon (#FFE600) hoặc trắng tinh với viền đen stroke dày 8px và bóng đổ (drop shadow), đặt trên nền tối hoặc dải badge tương phản.
4. Gợi tò mò tột độ (Curiosity Gap): Giới hạn 2-4 từ giật gân, không lặp lại nguyên văn tiêu đề mà khơi gợi câu hỏi chưa có lời đáp.
5. Hỗ trợ đa tỷ lệ: 16:9 (1280x720 cho video ngang/desktop) và 9:16 (1080x1920 cho Shorts Shelf & TikTok).
"""
import os
import re
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

# Danh sách font chữ đậm tối ưu cho thumbnail YouTube trên Windows
WINDOWS_FONTS = [
    "C:/Windows/Fonts/ariblk.ttf",     # Arial Black (Tối ưu nhất cho thumbnail)
    "C:/Windows/Fonts/impact.ttf",     # Impact
    "C:/Windows/Fonts/arialbd.ttf",    # Arial Bold
    "C:/Windows/Fonts/tahomabd.ttf",   # Tahoma Bold
    "C:/Windows/Fonts/seguisb.ttf",    # Segoe UI Semibold
]

# Bảng màu Two-Tone tương phản cao chuẩn @FossilVideos ("TOO EASY?"), @IFinity_What ("TURN INVISIBLE.") & @disastersexplained1
THUMBNAIL_PALETTES = {
    "yellow_danger": {"text": (255, 255, 255), "stroke": (0, 0, 0), "accent": (255, 230, 0)},
    "cyan_scifi": {"text": (255, 255, 255), "stroke": (5, 5, 20), "accent": (0, 240, 255)},
    "red_shock": {"text": (255, 255, 255), "stroke": (0, 0, 0), "accent": (255, 42, 42)},
    "gold_nature": {"text": (255, 255, 255), "stroke": (8, 8, 8), "accent": (255, 210, 0)},
}


class ThumbnailService:
    """Service tạo ảnh bìa tối ưu CTR theo quy tắc 1 giây (2-3 từ Two-Tone chuẩn @FossilVideos & @IFinity_What)."""

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or "storage/thumbnails")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.font_path = self._locate_best_font()

    def _locate_best_font(self) -> Optional[str]:
        for f in WINDOWS_FONTS:
            if os.path.exists(f):
                return f
        return None

    def compact_punchy_hook_text(
        self,
        raw_text: str,
        topic: str = "",
        viral_title: str = "",
        niche_id: str = "general",
    ) -> str:
        """
        Chuẩn hóa text trên Thumbnail về ĐÚNG 2–3 từ khóa ngắn gọn do LLM đề xuất (ALL CAPS, không emoji),
        chuẩn @FossilVideos ('TOO EASY?'), @IFinity_What ('TURN INVISIBLE.'), @disastersexplained1 ('EVERY RECORD').
        """
        cleaned = re.sub(r"#\S+", " ", str(raw_text or "")).strip()
        cleaned = re.sub(r"[^\w\s%$!?.,°:-]", "", cleaned).strip()
        words = [w for w in cleaned.upper().split() if w]
        if 2 <= len(words) <= 3:
            return " ".join(words)

        # Nếu câu quá dài (> 3 từ), rút gọn bỏ giới từ/mạo từ phụ để giữ 2–3 từ khóa chính
        if len(words) > 3:
            raw_up = " ".join(words)
            if raw_up.startswith("WHAT IF YOU"):
                after_wiy = re.sub(r"^WHAT\s+IF\s+YOU\s+(?:COULD\s+|NEVER\s+|HAD\s+TO\s+|WERE\s+)?", "", raw_up).strip()
                wiy_tokens = [w for w in after_wiy.split() if w.rstrip("!?.,:") not in {"THE", "A", "AN", "AT", "IN", "ON", "TO", "FOR", "WILL", "RIGHT", "NOW"}]
                if len(wiy_tokens) >= 2:
                    return f"{wiy_tokens[0]} {wiy_tokens[1].rstrip('!?.,:')}."

            stop_fillers = {
                "THE", "A", "AN", "IN", "ON", "AT", "OF", "TO", "FOR", "WITH", "FROM", "BY", "THIS", "THAT", "HOW", "WHY",
                "WHAT", "IF", "YOU", "COULD", "WOULD", "O", "OS", "AS", "DE", "DO", "DA", "DOS", "DAS", "NO", "NA", "EM", "UM", "UMA",
            }
            core_words = [w.rstrip("!?.,:") for w in words if w.rstrip("!?.,:") not in stop_fillers]
            if len(core_words) >= 2:
                return " ".join(core_words[:3])
            return " ".join(words[:3])

        return self.derive_curiosity_text(topic=topic, viral_title=viral_title, niche_id=niche_id)

    def derive_curiosity_text(self, topic: str, viral_title: str = "", niche_id: str = "general") -> str:
        """
        Rút trích 2-3 từ giật gân, gây tò mò tột độ cho Thumbnail theo đúng ngôn ngữ của tiêu đề (EN / pt-BR / VI).
        Không bao giờ in đơn vị vật lý phòng thí nghiệm (PSI, Newton, dB, Hz) lên Thumbnail.
        """
        combined = f"{viral_title} {topic}".strip()
        full_text = combined.lower()
        is_vi = bool(re.search(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", full_text))
        is_pt = not is_vi and bool(re.search(r"\b(onça|jacaré|lula|cachalote|oceano|mordida|recorde|predador|você|mundo|fossa)\b|[ãõç]", full_text))

        # 1. Chỉ trích xuất con số đời thường dễ hình dung (tốc độ km/h, mph, nhiệt độ °C/°F, thời gian hours/days), CẤM PSI/Newton/dB
        metric_m = re.search(r"\b(\d[\d.,]*\s*(?:km/h|mph|°c|°f|hours|days))\b", full_text, flags=re.IGNORECASE)
        if metric_m:
            met_str = metric_m.group(1).upper().strip()
            if len(met_str.split()) == 1:
                suffix = "FATAL!" if not is_vi else "TỬ THẦN!"
                return f"{met_str} {suffix}"
            return f"{met_str}!"

        # 2. Quy tắc chuẩn @FossilVideos / @disastersexplained1 / @IFinity_What theo ngôn ngữ & ngách
        if "every " in full_text and "record" in full_text:
            return "EVERY RECORD!"
        if "every " in full_text and ("type" in full_text or "shape" in full_text or "zone" in full_text):
            return "EVERY TYPE!"
        if "could a " in full_text or "beat every" in full_text or "dominate every" in full_text:
            return "TOO EASY?"
        if "what if you" in full_text:
            after_you = re.split(r"what if you\s+(?:could\s+|never\s+|had\s+to\s+)?", full_text, maxsplit=1)[-1]
            w_you = [w.upper() for w in re.findall(r"[a-zA-Z]{3,}", after_you) if w.lower() not in {"the", "in", "at", "for", "with", "into", "from"}][:2]
            if len(w_you) >= 2:
                return f"{w_you[0]} {w_you[1]}."

        if is_vi:
            if any(w in full_text for w in ["báo", "sư tử", "hổ", "săn mồi", "động vật", "wildlife", "predator"]):
                return "QUÁ DỄ DÀNG?"
            if any(w in full_text for w in ["tâm lý", "thao túng", "nắm thóp", "thẩm vấn"]):
                return "ĐỪNG CHỚP MẮT!"
            if any(w in full_text for w in ["giải cứu", "chó", "mèo", "rescue"]):
                return "ĐƯỢC CỨU SỐNG!"
            return "SỰ THẬT SỐC!"

        if is_pt:
            if any(w in full_text for w in ["onça", "jacaré", "sucuri", "predador", "animal", "wildlife"]):
                return "FÁCIL DEMAIS?"
            if any(w in full_text for w in ["oceano", "lula", "cachalote", "fossa", "mar", "mistério"]):
                return "RECORDE MORTAL!"
            return "ERRO FATAL!"

        if any(w in full_text for w in ["wildlife", "predator", "jaguar", "bear", "tiger", "lion", "eagle", "badger", "caiman"]):
            return "TOO EASY?"
        if any(w in full_text for w in ["ocean", "deep sea", "squid", "whale", "trench", "wave", "tornado", "volcano", "mystery"]):
            return "DEADLIEST RECORD"
        if any(w in full_text for w in ["psychology", "fbi", "interrogation", "lie", "brain", "chamber", "sleep"]):
            return "DO NOT BLINK"
        if any(w in full_text for w in ["rescue", "puppy", "dog", "kitten", "stray", "shelter"]):
            return "FINALLY SAFE"

        cleaned_t = re.sub(r"#\S+", " ", viral_title or topic)
        words = [w for w in re.sub(r"[^\w\s%$-]", " ", cleaned_t).split() if len(w) >= 3]
        if len(words) >= 2:
            return " ".join(words[:3]).upper().rstrip("!?.") + "!"
        return "FATAL MISTAKE!"

    def extract_video_frame(self, video_path: str, timestamp_sec: float = 1.5, output_img_path: str = "") -> Optional[str]:
        """Trích xuất 1 khung hình sắc nét nhất từ video MP4 đã render bằng FFmpeg."""
        import subprocess
        if not os.path.exists(video_path):
            return None

        out_path = output_img_path or str(self.output_dir / f"frame_{Path(video_path).stem}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp_sec),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 3000:
                return out_path
        except Exception as e:
            logger.debug(f"Frame extraction error: {e}")
        return None

    def _select_sharpest_clean_broll_frame(self, video_path: str, job_id: int, title: str = "") -> Optional[str]:
        """
        Quét các file B-Roll gốc sạch (chưa đóng phụ đề hay badge) trong `media/scene_XXX_16x9.mp4` bằng FFmpeg + PIL
        (không phụ thuộc cv2), kết hợp đối sánh Thực thể chính trên Tiêu đề (Primary Entity Match) + Độ sắc nét vùng trung tâm
        để chọn ra khung hình 16:9 bắt mắt nhất làm nền Thumbnail.
        """
        import subprocess
        import tempfile
        from PIL import ImageStat

        try:
            from config.settings import settings
            candidate_dirs = []
            if video_path and os.path.exists(video_path):
                v_dir = Path(video_path).parent
                if (v_dir / "media").exists():
                    candidate_dirs.append(v_dir / "media")
                if v_dir.name == "media" and v_dir.exists():
                    candidate_dirs.append(v_dir)
            if job_id:
                temp_media_dir = Path(settings.TEMP_DIR) / f"job_{job_id}" / "media"
                if temp_media_dir.exists() and temp_media_dir not in candidate_dirs:
                    candidate_dirs.append(temp_media_dir)

            # Đọc metadata kịch bản để biết phân cảnh nào chứa Thực thể chính (Primary Subject) trên tiêu đề
            scene_kw_map: Dict[int, str] = {}
            if job_id:
                try:
                    import json as _json
                    from app.database.models import SessionLocal, VideoJob
                    db = SessionLocal()
                    try:
                        j_row = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                        if j_row:
                            if not title:
                                title = str(j_row.title or j_row.topic or "")
                            sc_obj = _json.loads(j_row.scenes_json or "{}")
                            sc_items = sc_obj if isinstance(sc_obj, list) else (sc_obj.get("scenes") or [])
                            for idx_s, s_item in enumerate(sc_items, 1):
                                if isinstance(s_item, dict):
                                    s_id = int(s_item.get("scene_id") or idx_s)
                                    scene_kw_map[s_id] = str(
                                        s_item.get("visual_keyword", "")
                                    ).lower()
                    finally:
                        db.close()
                    if not scene_kw_map:
                        ckpt_p = Path(settings.TEMP_DIR) / f"job_{job_id}" / "step_checkpoint.json"
                        if ckpt_p.exists():
                            ck_obj = _json.loads(ckpt_p.read_text(encoding="utf-8"))
                            nodes_d = ck_obj.get("nodes") or ck_obj.get("steps") or {}
                            for n_key in ("fetch_media", "visual_director", "generate_audio"):
                                sc_list = (nodes_d.get(n_key) or {}).get("scenes_media") or (
                                    ((nodes_d.get(n_key) or {}).get("script_data") or {}).get("scenes")
                                ) or []
                                for s_item in sc_list:
                                    if isinstance(s_item, dict) and s_item.get("scene_id"):
                                        scene_kw_map[int(s_item["scene_id"])] = str(
                                            s_item.get("visual_keyword", "")
                                        ).lower()
                except Exception:
                    pass

            # Trích xuất từ khóa Thực thể chính từ visual_keyword của Cảnh 1 (luôn cùng ngôn ngữ EN với visual_keyword của các cảnh)
            stop_words = {
                "the", "and", "for", "with", "inside", "how", "why", "what", "when", "who", "from", "into",
                "water", "river", "ocean", "deep", "dark", "wildlife", "closeup", "close", "slow", "motion",
                "swimming", "walking", "stalking", "hunting", "underwater", "blue", "amazon", "forest", "riverbank",
                "tree", "branch", "watching",
            }
            s1_kw = scene_kw_map.get(1, "")
            hero_tokens = [w for w in re.findall(r"[a-zA-Z]{4,}", s1_kw) if w not in stop_words][:2]

            candidate_clips = []
            for media_dir in candidate_dirs:
                for p_clip in (
                    sorted(media_dir.glob("scene_*_16x9.mp4"))
                    + sorted(media_dir.glob("scene_*.mp4"))
                    + sorted(media_dir.glob("scene_*.jpg"))
                ):
                    name_low = p_clip.name.lower()
                    if (
                        p_clip.exists()
                        and p_clip.stat().st_size > 35_000
                        and not name_low.endswith(("_doodle.mp4", "_doodle_2d.mp4", "_prerender_rescue.mp4"))
                        and str(p_clip) not in candidate_clips
                    ):
                        candidate_clips.append(str(p_clip))
            if not candidate_clips:
                return None

            best_score = -1.0
            best_img_path = None
            with tempfile.TemporaryDirectory() as tmp_d:
                for idx_c, clip_p in enumerate(candidate_clips[:24]):
                    clip_name = Path(clip_p).name.lower()
                    m_sc = re.search(r"scene_0*(\d+)", clip_name)
                    sc_num = int(m_sc.group(1)) if m_sc else (idx_c + 1)
                    kw_text = scene_kw_map.get(sc_num, "")

                    # Ưu tiên phân cảnh có mặt Thực thể chính (Hero Entity) trong visual_keyword
                    entity_mult = 1.0
                    if hero_tokens and kw_text:
                        if any(tok in kw_text for tok in hero_tokens):
                            entity_mult = 2.5
                        else:
                            entity_mult = 0.22
                    elif sc_num in (1, 2):
                        entity_mult = 1.45

                    is_static_img = clip_name.endswith((".jpg", ".jpeg", ".png"))
                    probe_timestamps = (0.0,) if is_static_img else (0.6, 1.4, 2.3)
                    for t_idx, ts_sec in enumerate(probe_timestamps):
                        if is_static_img:
                            probe_jpg = clip_p
                        else:
                            probe_jpg = str(Path(tmp_d) / f"cand_{sc_num}_{t_idx}.jpg")
                            subprocess.run(
                                ["ffmpeg", "-v", "error", "-y", "-ss", str(ts_sec), "-i", clip_p, "-frames:v", "1", "-q:v", "2", probe_jpg],
                                capture_output=True,
                                timeout=6,
                                check=False,
                            )
                        if not os.path.exists(probe_jpg) or os.path.getsize(probe_jpg) < 4000:
                            continue
                        with Image.open(probe_jpg) as im_p:
                            rgb_p = im_p.convert("RGB").resize((320, 180))
                            g_p = rgb_p.convert("L")
                            luma_mean = float(ImageStat.Stat(g_p).mean[0])
                            if luma_mean < 28.0 or luma_mean > 225.0:
                                continue
                            # Chấm điểm vùng chủ thể trung tâm (Center ROI) + Độ bão hòa màu sắc, phạt nhiễu lưới/cành cây dày đặc (edge_mean > 32)
                            roi_g = g_p.crop((64, 32, 256, 152))
                            roi_edges = roi_g.filter(ImageFilter.FIND_EDGES)
                            edge_mean = float(ImageStat.Stat(roi_edges).mean[0])
                            contrast_std = float(ImageStat.Stat(roi_g).stddev[0])
                            hsv_p = rgb_p.convert("HSV")
                            sat_mean = float(ImageStat.Stat(hsv_p.crop((64, 32, 256, 152))).mean[1])
                            lead_scene_bonus = 1.45 if sc_num == 1 else (1.20 if sc_num in (2, 4) else 1.0)
                            mesh_penalty = 0.55 if edge_mean > 31.0 else 1.0
                            score = (min(26.0, edge_mean) * 1.2 + contrast_std * 1.65 + sat_mean * 0.45) * entity_mult * lead_scene_bonus * mesh_penalty
                            if score > best_score:
                                best_score = score
                                out_clean = str(self.output_dir / f"clean_broll_thumb_base_{job_id}.jpg")
                                im_p.convert("RGB").save(out_clean, "JPEG", quality=96)
                                best_img_path = out_clean

            if best_img_path and os.path.exists(best_img_path) and os.path.getsize(best_img_path) > 5000:
                return best_img_path
        except Exception as e_best:
            logger.debug(f"Clean B-roll thumbnail frame selection skip: {e_best}")
        return None

    def render_thumbnail(
        self,
        base_image_path: str,
        text_hook: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        palette_key: str = "yellow_danger"
    ) -> Optional[str]:
        """
        Vẽ chữ to, viền stroke dày và hiệu ứng tương phản cao lên ảnh bìa.
        aspect_ratio: '16:9' (1280x720) hoặc '9:16' (1080x1920).
        """
        if not os.path.exists(base_image_path):
            return None

        try:
            im = Image.open(base_image_path).convert("RGBA")
            target_w, target_h = (1280, 720) if aspect_ratio == "16:9" else (1080, 1920)

            # Resize & Crop chuẩn tỷ lệ
            im = self._crop_to_aspect(im, target_w, target_h)

            # Tăng độ sắc nét chi tiết (UnsharpMask), độ bão hòa màu (+22%) và độ tương phản (+16%) để ảnh bìa nổi khối bắt mắt
            rgb_base = im.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.8, percent=135, threshold=3))
            im = rgb_base.convert("RGBA")
            enh_col = ImageEnhance.Color(im)
            im = enh_col.enhance(1.22)
            enh_con = ImageEnhance.Contrast(im)
            im = enh_con.enhance(1.16)

            # Chuẩn hóa text_hook về ĐÚNG 2–3 từ giật gân (không emoji, ALL CAPS) chuẩn @FossilVideos & @IFinity_What
            clean_text_hook = self.compact_punchy_hook_text(text_hook)
            words = clean_text_hook.upper().split()
            if not words:
                words = ["TOO", "EASY?"]

            if len(words) <= 2:
                lines = [" ".join(words)]
            elif len(words) == 3:
                # Nếu tổng số ký tự ngắn (<= 14 chars như "TOO EASY NOW?"), để 1 dòng; nếu dài hơn, tách 2 dòng cân đối
                joined_3 = " ".join(words)
                if len(joined_3) <= 14 and aspect_ratio == "16:9":
                    lines = [joined_3]
                else:
                    lines = [" ".join(words[:2]), words[2]]
            else:
                lines = [" ".join(words[:2]), " ".join(words[2:4])]

            # Dynamic font auto-fit: Chữ siêu to (128px cho 16:9, 118px cho 9:16) nằm gọn trong 86% chiều rộng
            draw = ImageDraw.Draw(im)
            max_allowed_w = int(target_w * 0.86)
            init_font_size = 128 if aspect_ratio == "16:9" else 118
            stroke_w = 10

            font = None
            line_widths = []
            line_heights = []

            for f_size in range(init_font_size, 44, -4):
                if self.font_path:
                    try:
                        cand_font = ImageFont.truetype(self.font_path, f_size)
                    except Exception:
                        cand_font = ImageFont.load_default()
                else:
                    cand_font = ImageFont.load_default()

                temp_widths = []
                temp_heights = []
                too_wide = False
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=cand_font, stroke_width=stroke_w)
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
                    temp_widths.append(w)
                    temp_heights.append(h)
                    if w > max_allowed_w:
                        too_wide = True
                        break

                if not too_wide:
                    font = cand_font
                    line_widths = temp_widths
                    line_heights = temp_heights
                    break

            if not font:
                font = cand_font
                line_widths = temp_widths
                line_heights = temp_heights

            pal = THUMBNAIL_PALETTES.get(palette_key, THUMBNAIL_PALETTES["yellow_danger"])
            text_color = pal["text"]        # Pure White (255, 255, 255)
            stroke_color = pal["stroke"]    # Pitch Black (0, 0, 0)
            accent_color = pal.get("accent", (255, 230, 0))  # High-Voltage Yellow or Crimson Red

            # Tự động nhận diện vị trí mặt/mắt chủ thể (Upper-Center vs Lower-Center) để đặt chữ KHÔNG BAO GIỜ che mắt chủ thể
            place_at_bottom = False
            if aspect_ratio == "16:9":
                try:
                    from PIL import ImageStat
                    edges_map = im.convert("L").filter(ImageFilter.FIND_EDGES)
                    up_box = (int(target_w * 0.32), int(target_h * 0.10), int(target_w * 0.68), int(target_h * 0.34))
                    dn_box = (int(target_w * 0.32), int(target_h * 0.68), int(target_w * 0.68), int(target_h * 0.92))
                    up_edge = float(ImageStat.Stat(edges_map.crop(up_box)).mean[0])
                    dn_edge = float(ImageStat.Stat(edges_map.crop(dn_box)).mean[0])
                    # Nếu vùng đầu/mặt chính giữa phía trên (y=10%..34%) có chi tiết chủ thể (up_edge >= 12.5), chuyển chữ xuống đáy để mở trọn đôi mắt chủ thể!
                    if up_edge >= 12.5 or (up_edge > dn_edge * 1.05 and up_edge >= 9.5):
                        place_at_bottom = True
                except Exception:
                    place_at_bottom = False

            total_text_block_h = sum(line_heights) + max(0, len(lines) - 1) * 18
            if place_at_bottom:
                start_y = max(int(target_h * 0.55), target_h - total_text_block_h - int(target_h * 0.085))
            else:
                start_y = int(target_h * 0.065) if aspect_ratio == "16:9" else int(target_h * 0.14)

            # Phủ dải Vignette Gradient mượt ở vùng đặt chữ (trên hoặc dưới, không dùng hộp vuông thô che mặt chủ thể!)
            grad_overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            g_draw = ImageDraw.Draw(grad_overlay)
            if place_at_bottom:
                grad_start_y = max(0, start_y - 80)
                span_h = max(1.0, float(target_h - grad_start_y))
                for y_pos in range(grad_start_y, target_h):
                    progress = (y_pos - grad_start_y) / span_h
                    alpha = int(195 * (progress ** 1.30))
                    if alpha > 0:
                        g_draw.line([(0, y_pos), (target_w, y_pos)], fill=(0, 0, 0, alpha))
            else:
                grad_end_y = min(target_h, start_y + total_text_block_h + 75)
                for y_pos in range(0, grad_end_y):
                    progress = y_pos / max(1.0, float(grad_end_y))
                    alpha = int(190 * ((1.0 - progress) ** 1.35))
                    if alpha > 0:
                        g_draw.line([(0, y_pos), (target_w, y_pos)], fill=(0, 0, 0, alpha))

            im = Image.alpha_composite(im, grad_overlay)
            draw = ImageDraw.Draw(im)

            # Vẽ chữ Two-Tone từng từ (Word 1: Trắng Tinh #FFFFFF, Từ Cuối / Con Số: Vàng/Đỏ Accent)
            space_bbox = draw.textbbox((0, 0), " ", font=font, stroke_width=0)
            space_w = max(14, (space_bbox[2] - space_bbox[0]))

            for idx, line in enumerate(lines):
                lh = line_heights[idx]
                cur_y = start_y + idx * (lh + 18)
                line_words = line.split()

                # Tính tổng chiều rộng từng từ trên dòng để căn giữa hoàn hảo
                word_Advance_list = []
                for w_tok in line_words:
                    wb = draw.textbbox((0, 0), w_tok, font=font, stroke_width=0)
                    word_Advance_list.append(wb[2] - wb[0])
                total_line_w = sum(word_Advance_list) + max(0, len(line_words) - 1) * space_w
                cur_x = int((target_w - total_line_w) / 2)

                for w_idx, w_tok in enumerate(line_words):
                    # Quy tắc Two-Tone chuẩn @FossilVideos / @IFinity_What:
                    # - Nếu có 1 dòng (>= 2 từ): từ đầu màu Trắng (#FFFFFF), từ cuối mang màu Accent (#FFD800 / #FF2A2A).
                    # - Nếu có >= 2 dòng: dòng đầu màu Trắng (#FFFFFF), dòng cuối mang màu Accent.
                    if len(lines) == 1:
                        is_accent = (w_idx == len(line_words) - 1 and len(line_words) > 1)
                    else:
                        is_accent = (idx == len(lines) - 1)
                    w_fill = accent_color if is_accent else text_color

                    # 1. Bóng đổ sâu (Deep Drop Shadow)
                    draw.text(
                        (cur_x + 7, cur_y + 8),
                        w_tok,
                        font=font,
                        fill=(0, 0, 0, 255),
                        stroke_width=stroke_w,
                        stroke_fill=(0, 0, 0),
                    )
                    # 2. Chữ chính với viền đen dày 10px
                    draw.text(
                        (cur_x, cur_y),
                        w_tok,
                        font=font,
                        fill=w_fill,
                        stroke_width=stroke_w,
                        stroke_fill=stroke_color,
                    )
                    cur_x += word_Advance_list[w_idx] + space_w

            # Lưu ảnh kết quả chất lượng cao (JPEG 95%)
            rgb_im = im.convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            rgb_im.save(output_path, "JPEG", quality=95, optimize=True)
            logger.info(f"🎨 [ThumbnailService] Đã tạo Thumbnail ({aspect_ratio}) thành công: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Thumbnail render error: {e}")
            return None

    def _crop_to_aspect(self, im: Image.Image, target_w: int, target_h: int) -> Image.Image:
        """Cắt ảnh theo chuẩn tỷ lệ target_w x target_h ở tâm ảnh."""
        src_w, src_h = im.size
        src_aspect = src_w / src_h
        target_aspect = target_w / target_h

        if src_aspect > target_aspect:
            new_w = int(src_h * target_aspect)
            offset = (src_w - new_w) // 2
            im = im.crop((offset, 0, offset + new_w, src_h))
        else:
            new_h = int(src_w / target_aspect)
            offset = (src_h - new_h) // 2
            im = im.crop((0, offset, src_w, offset + new_h))

        return im.resize((target_w, target_h), Image.Resampling.LANCZOS)

    def generate_viral_thumbnail(
        self,
        job_id: int,
        topic: str,
        viral_title: str = "",
        video_path: Optional[str] = None,
        base_image_path: Optional[str] = None,
        niche_id: str = "general",
        custom_text: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Quy trình trọn gói tự động tạo 2 phiên bản Thumbnail (16:9 và 9:16) cho 1 VideoJob.
        Trả về: {'thumb_16x9': path, 'thumb_9x16': path, 'text_hook': str}
        """
        text_hook = self.compact_punchy_hook_text(
            raw_text=str(custom_text or ""),
            topic=topic,
            viral_title=viral_title,
            niche_id=niche_id,
        )

        # Lựa chọn ảnh gốc: Ưu tiên ảnh gốc sạch (Clean Image) chất lượng cao không dính subtitle/header!
        chosen_base = None
        if base_image_path and os.path.exists(str(base_image_path)):
            chosen_base = str(base_image_path)
        elif video_path and os.path.exists(video_path):
            # Ưu tiên số 1: Quét chọn khung hình B-Roll gốc sắc nét & tương phản cao nhất (chưa dính phụ đề/badge)
            chosen_base = self._select_sharpest_clean_broll_frame(video_path, job_id, title=viral_title or topic)
            if not chosen_base:
                v_dir = Path(video_path).parent
                theme_frames = sorted(v_dir.glob("_theme_frame_*.jpg"))
                if theme_frames and theme_frames[0].exists():
                    chosen_base = str(theme_frames[0])
                else:
                    chosen_base = self.extract_video_frame(video_path, timestamp_sec=0.04)

        if not chosen_base or not os.path.exists(chosen_base):
            # Nếu không có ảnh, tạo nền gradient tối sang trọng
            bg = Image.new("RGB", (1280, 720), color=(15, 18, 28))
            chosen_base = str(self.output_dir / f"temp_base_{job_id}.jpg")
            bg.save(chosen_base)

        palette = "yellow_danger"
        if "scifi" in niche_id:
            palette = "cyan_scifi"
        elif "wildlife" in niche_id:
            palette = "gold_nature"
        elif "psychology" in niche_id:
            palette = "red_shock"

        p_16x9 = str(self.output_dir / f"thumb_{job_id}_16x9.jpg")
        p_9x16 = str(self.output_dir / f"thumb_{job_id}_9x16.jpg")

        self.render_thumbnail(chosen_base, text_hook, p_16x9, aspect_ratio="16:9", palette_key=palette)
        self.render_thumbnail(chosen_base, text_hook, p_9x16, aspect_ratio="9:16", palette_key=palette)

        return {
            "thumb_16x9": p_16x9 if os.path.exists(p_16x9) else "",
            "thumb_9x16": p_9x16 if os.path.exists(p_9x16) else "",
            "16x9": p_16x9 if os.path.exists(p_16x9) else "",
            "9x16": p_9x16 if os.path.exists(p_9x16) else "",
            "text_hook": text_hook
        }

    def generate_thumbnails(
        self,
        job_id: int,
        title: str,
        video_path: Optional[str] = None,
        background_image_path: Optional[str] = None,
        custom_text: Optional[str] = None,
        frame_timestamp_s: float = 1.5,
        niche_id: str = "general"
    ) -> Dict[str, str]:
        """
        Phương thức đồng bộ đa năng tạo Thumbnail 16:9 và 9:16.
        Hỗ trợ custom text hoặc tự động suy luận hook 1 giây từ tiêu đề.
        """
        text_hook = self.compact_punchy_hook_text(
            raw_text=str(custom_text or ""),
            topic=title,
            viral_title=title,
            niche_id=niche_id,
        )

        chosen_base = None
        if background_image_path and os.path.exists(str(background_image_path)):
            chosen_base = str(background_image_path)
        elif video_path and os.path.exists(video_path):
            chosen_base = self._select_sharpest_clean_broll_frame(video_path, job_id, title=title)
            if not chosen_base:
                v_dir = Path(video_path).parent
                theme_frames = sorted(v_dir.glob("_theme_frame_*.jpg"))
                if theme_frames and theme_frames[0].exists():
                    chosen_base = str(theme_frames[0])
                else:
                    chosen_base = self.extract_video_frame(video_path, timestamp_sec=frame_timestamp_s if frame_timestamp_s < 0.2 else 0.04)

        if not chosen_base or not os.path.exists(chosen_base):
            bg = Image.new("RGB", (1280, 720), color=(15, 18, 28))
            chosen_base = str(self.output_dir / f"temp_base_{job_id}.jpg")
            bg.save(chosen_base)

        palette = "yellow_danger"
        if "scifi" in niche_id:
            palette = "cyan_scifi"
        elif "wildlife" in niche_id:
            palette = "gold_nature"
        elif "psychology" in niche_id:
            palette = "red_shock"

        p_16x9 = str(self.output_dir / f"thumb_{job_id}_16x9.jpg")
        p_9x16 = str(self.output_dir / f"thumb_{job_id}_9x16.jpg")

        self.render_thumbnail(chosen_base, text_hook, p_16x9, aspect_ratio="16:9", palette_key=palette)
        self.render_thumbnail(chosen_base, text_hook, p_9x16, aspect_ratio="9:16", palette_key=palette)

        res_16x9 = p_16x9 if os.path.exists(p_16x9) else ""
        res_9x16 = p_9x16 if os.path.exists(p_9x16) else ""

        return {
            "16x9": res_16x9,
            "9x16": res_9x16,
            "thumb_16x9": res_16x9,
            "thumb_9x16": res_9x16,
            "text_hook": text_hook
        }
