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
        Chuẩn hóa text trên Thumbnail về ĐÚNG 2–3 từ khóa giật gân, dễ hình dung nhất (ALL CAPS, không emoji),
        cấm dùng đơn vị vật lý khô khan (PSI, Newton, kPa, dB, Hz), ưu tiên cụm động từ/ẩn dụ thị giác mạnh
        chuẩn @FossilVideos ('TOO EASY?'), @IFinity_What ('TURN INVISIBLE.'), @disastersexplained1 ('EVERY RECORD').
        """
        banned_lab_units = {
            "PSI", "NEWTON", "NEWTONS", "KPA", "MPA", "JOULE", "JOULES",
            "DB", "DBA", "HZ", "KHZ", "LD50", "VEI", "VOLT", "VOLTS", "MEGATON", "MEGATONS",
        }
        cleaned = re.sub(r"#\S+", " ", str(raw_text or "")).strip()
        cleaned = re.sub(r"[^\w\s%$!?.,°:-]", "", cleaned).strip()
        words = [w for w in cleaned.upper().split() if w]
        if 2 <= len(words) <= 3:
            if not any(w.rstrip("!?.,:") in banned_lab_units for w in words):
                return " ".join(words)

        # Nếu câu quá dài (> 3 từ) hoặc dính đơn vị vật lý khô khan, rút trích cụm động từ/danh từ giàu hình ảnh hoặc số đo đời thường
        if len(words) >= 2:
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
            for idx_w in range(len(words) - 1):
                if any(ch.isdigit() for ch in words[idx_w]):
                    unit_w = words[idx_w + 1].rstrip("!?.,:")
                    if unit_w not in stop_fillers and unit_w not in banned_lab_units:
                        return f"{words[idx_w]} {unit_w}!"
            core_words = [
                w.rstrip("!?.,:") for w in words
                if w.rstrip("!?.,:") not in stop_fillers
                and w.rstrip("!?.,:") not in banned_lab_units
                and not any(ch.isdigit() for ch in w)
            ]
            if len(core_words) >= 2:
                return " ".join(core_words[:3])
            if len(words) > 3:
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

    def _select_sharpest_clean_broll_frame(self, video_path: str, job_id: int) -> Optional[str]:
        """
        Quét các file B-Roll gốc sạch (chưa đóng phụ đề hay badge '[ // CHAPTER 1 // ]') trong `media/scene_XXX_16x9.mp4`
        và chấm điểm Độ sắc nét (Laplacian Variance) + Độ tương phản để chọn ra khung hình 16:9 bắt mắt nhất làm nền Thumbnail.
        """
        if not video_path or not os.path.exists(video_path):
            return None
        try:
            import cv2
            v_dir = Path(video_path).parent
            media_dir = v_dir / "media"
            candidate_clips = []
            if media_dir.exists():
                for pat in ("scene_001_16x9.mp4", "scene_002_16x9.mp4", "scene_004_16x9.mp4", "scene_005_16x9.mp4", "scene_001*.mp4"):
                    for p_clip in sorted(media_dir.glob(pat)):
                        if p_clip.exists() and p_clip.stat().st_size > 40_000 and str(p_clip) not in candidate_clips:
                            candidate_clips.append(str(p_clip))
            if not candidate_clips:
                return None

            best_score = -1.0
            best_frame_bgr = None
            for clip_p in candidate_clips[:4]:
                cap = cv2.VideoCapture(clip_p)
                if not cap.isOpened():
                    continue
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
                total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                for ratio in (0.22, 0.45, 0.68):
                    target_f = max(1, min(total_f - 2, int(total_f * ratio))) if total_f > 10 else int(fps * 1.2)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    mean_luma = float(gray.mean())
                    if mean_luma < 28.0 or mean_luma > 235.0:
                        continue
                    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    contrast = float(gray.std())
                    score = sharpness * 0.65 + contrast * 1.35
                    if score > best_score:
                        best_score = score
                        best_frame_bgr = frame.copy()
                cap.release()

            if best_frame_bgr is not None:
                out_clean = str(self.output_dir / f"clean_broll_thumb_base_{job_id}.jpg")
                cv2.imwrite(out_clean, best_frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 96])
                if os.path.exists(out_clean) and os.path.getsize(out_clean) > 5000:
                    return out_clean
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

            # Tăng độ tương phản nhẹ và độ bão hòa màu để ảnh hút mắt hơn (+18% saturation, +14% contrast)
            enh_col = ImageEnhance.Color(im)
            im = enh_col.enhance(1.18)
            enh_con = ImageEnhance.Contrast(im)
            im = enh_con.enhance(1.14)

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

            start_y = int(target_h * 0.08) if aspect_ratio == "16:9" else int(target_h * 0.14)

            # Phủ dải Vignette Gradient mượt ở phần trên ảnh (không dùng hộp vuông thô che mặt chủ thể!)
            grad_overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            g_draw = ImageDraw.Draw(grad_overlay)
            grad_end_y = min(target_h, start_y + sum(line_heights) + len(lines) * 28 + 70)
            for y_pos in range(0, grad_end_y):
                progress = y_pos / max(1.0, float(grad_end_y))
                alpha = int(185 * ((1.0 - progress) ** 1.35))
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
            chosen_base = self._select_sharpest_clean_broll_frame(video_path, job_id)
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
            chosen_base = self._select_sharpest_clean_broll_frame(video_path, job_id)
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
