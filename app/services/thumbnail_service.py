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

# Bảng màu tương phản cao bắt mắt nhất trên YouTube
THUMBNAIL_PALETTES = {
    "yellow_danger": {"text": (255, 230, 0), "stroke": (0, 0, 0), "accent": (255, 50, 50)},
    "cyan_scifi": {"text": (0, 240, 255), "stroke": (5, 5, 20), "accent": (255, 230, 0)},
    "red_shock": {"text": (255, 255, 255), "stroke": (0, 0, 0), "accent": (255, 40, 40)},
    "gold_nature": {"text": (255, 185, 0), "stroke": (10, 10, 10), "accent": (255, 255, 255)},
}


class ThumbnailService:
    """Service tạo ảnh bìa tối ưu CTR theo quy tắc 1 giây."""

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or "storage/thumbnails")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.font_path = self._locate_best_font()

    def _locate_best_font(self) -> Optional[str]:
        for f in WINDOWS_FONTS:
            if os.path.exists(f):
                return f
        return None

    def derive_curiosity_text(self, topic: str, viral_title: str = "", niche_id: str = "general") -> str:
        """
        Rút trích 2-4 từ giật gân, gây tò mò tột độ cho Thumbnail (không dài dòng).
        Ví dụ: 'SỰ THẬT TÀN KHỐC!', 'ĐỪNG LÀM ĐIỀU NÀY!', '0.01% BIẾT ĐIỀU NÀY', 'CÚ SỐC THỊ GIÁC'.
        """
        full_text = f"{viral_title} {topic}".lower()

        # Quy tắc theo từng ngách
        if any(w in full_text for w in ["báo", "sư tử", "hổ", "chim", "săn mồi", "động vật", "wildlife", "predator"]):
            if "tốc độ" in full_text or "báo" in full_text:
                return "TỐC ĐỘ KINH HOÀNG!"
            if "chim" in full_text or "nuôi con" in full_text:
                return "SỰ THẬT TÀN KHỐC!"
            return "CÚ SĂN MỒI NGHẸT THỞ!"

        if any(w in full_text for w in ["vũ trụ", "hố đen", "viễn tưởng", "space", "alien", "scifi", "ngân hà"]):
            if "hố đen" in full_text:
                return "ĐIỀU KINH HOÀNG NHẤT!"
            return "VƯỢT TRẢI NGHIỆM THƯỜNG!"

        if any(w in full_text for w in ["tâm lý", "thao túng", "nắm thóp", "điểm yếu", "khắc kỷ", "tranh luận"]):
            if "nắm thóp" in full_text or "điểm yếu" in full_text:
                return "ĐỪNG ĐỂ BỊ NẮM THÓP!"
            return "0.01% NGƯỜI BIẾT!"

        if any(w in full_text for w in ["giải cứu", "chó", "mèo", "bỏ rơi", "rescue"]):
            return "PHÉP MÀU KỲ DIỆU!"

        # Fallback từ tiêu đề ngắn (giữ nguyên ký hiệu %, $, - gắn liền với con số như 0%, 3-Second)
        cleaned_t = re.sub(r"#\S+", " ", viral_title or topic)
        words = re.sub(r"[^\w\s%$-]", " ", cleaned_t).split()
        if len(words) >= 3:
            return " ".join(words[:4] if len(words[0]) <= 3 else words[:3]).upper().rstrip("!?.") + "!"
        return "SỰ THẬT BẤT NGỜ!"

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

            # Tăng độ tương phản nhẹ và độ bão hòa màu để ảnh hút mắt hơn (+15% saturation, +10% contrast)
            enh_col = ImageEnhance.Color(im)
            im = enh_col.enhance(1.18)
            enh_con = ImageEnhance.Contrast(im)
            im = enh_con.enhance(1.12)

            draw = ImageDraw.Draw(im)
            # Loại bỏ emoji khỏi text_hook để tránh lỗi hiển thị ô vuông (tofu glyph) trên các font TTF
            clean_text_hook = re.sub(r'[^\w\s\?!.,:;\'"-]+', '', text_hook).strip()
            if not clean_text_hook:
                clean_text_hook = text_hook.strip()

            # Tự động chia dòng nếu text dài
            words = clean_text_hook.upper().split()
            lines = []
            if len(words) <= 3:
                lines = [" ".join(words)]
            elif len(words) == 4:
                lines = [" ".join(words[:2]), " ".join(words[2:])]
            elif len(words) <= 6:
                lines = [" ".join(words[:3]), " ".join(words[3:])]
            else:
                lines = [" ".join(words[:2]), " ".join(words[2:4]), " ".join(words[4:])]

            # Dynamic font auto-fit: Đảm bảo 100% dòng chữ nằm gọn trong 82% chiều rộng khung hình (chống tràn viền)
            max_allowed_w = int(target_w * 0.82)
            init_font_size = 85 if aspect_ratio == "16:9" else 95

            font = None
            line_widths = []
            line_heights = []

            for f_size in range(init_font_size, 38, -3):
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
                    bbox = draw.textbbox((0, 0), line, font=cand_font, stroke_width=8)
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
            text_color = pal["text"]
            stroke_color = pal["stroke"]
            accent_color = pal.get("accent", (255, 50, 50))

            total_text_h = sum(line_heights) + (len(lines) - 1) * 15
            start_y = int(target_h * 0.18) if aspect_ratio == "16:9" else int(target_h * 0.22)

            # Vẽ dải gradient tối hoặc banner đen mờ phía sau text để bảo đảm 100% chữ đọc rõ trong 1 giây
            banner_overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            b_draw = ImageDraw.Draw(banner_overlay)
            pad_y = 25
            for idx, line in enumerate(lines):
                lw = line_widths[idx]
                lh = line_heights[idx]
                cur_y = start_y + idx * (lh + 20)
                cur_x = int((target_w - lw) / 2)

                # Vẽ dải badge nền đen bo tròn tương phản cao phía sau từng dòng chữ
                box_x1 = max(cur_x - 30, 20)
                box_y1 = cur_y - 12
                box_x2 = min(cur_x + lw + 30, target_w - 20)
                box_y2 = cur_y + lh + 14
                b_draw.rounded_rectangle(
                    [box_x1, box_y1, box_x2, box_y2],
                    radius=16,
                    fill=(0, 0, 0, 210)  # Đen mờ 82% tương phản cực mạnh
                )

            # Hòa trộn dải banner đen mờ vào ảnh
            im = Image.alpha_composite(im, banner_overlay)
            draw = ImageDraw.Draw(im)

            # Vẽ chữ to có viền đen dày và hiệu ứng bóng đổ
            for idx, line in enumerate(lines):
                lw = line_widths[idx]
                lh = line_heights[idx]
                cur_y = start_y + idx * (lh + 20)
                cur_x = int((target_w - lw) / 2)

                # Đổi màu dòng cuối sang màu Accent (Đỏ hoặc Vàng) để tạo điểm nhấn thị giác
                curr_fill = accent_color if (idx == len(lines) - 1 and len(lines) > 1) else text_color

                # Vẽ bóng đổ (Drop shadow)
                draw.text(
                    (cur_x + 6, cur_y + 6),
                    line,
                    font=font,
                    fill=(0, 0, 0, 255),
                    stroke_width=6,
                    stroke_fill=(0, 0, 0)
                )
                # Vẽ chữ chính với viền đen dày 8px
                draw.text(
                    (cur_x, cur_y),
                    line,
                    font=font,
                    fill=curr_fill,
                    stroke_width=8,
                    stroke_fill=stroke_color
                )

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
        clean_custom = re.sub(r"[^\w\s%$!?.:-]", "", str(custom_text or "")).strip()
        text_hook = clean_custom.upper() if len(clean_custom) >= 4 else self.derive_curiosity_text(topic=topic, viral_title=viral_title, niche_id=niche_id)

        # Lựa chọn ảnh gốc: Ưu tiên ảnh gốc sạch (Clean Image) chất lượng cao không dính subtitle/header!
        chosen_base = None
        if base_image_path and os.path.exists(str(base_image_path)):
            chosen_base = str(base_image_path)
        elif video_path and os.path.exists(video_path):
            # Kiểm tra xem có ảnh _theme_frame gốc trong thư mục temp không
            v_dir = Path(video_path).parent
            theme_frames = sorted(v_dir.glob("_theme_frame_*.jpg"))
            if theme_frames and theme_frames[0].exists():
                chosen_base = str(theme_frames[0])
            else:
                # Nếu buộc phải trích xuất từ video, lấy frame tại 0.04s (trước khi bất kỳ phụ đề hay banner nào xuất hiện)
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
        text_hook = custom_text or self.derive_curiosity_text(topic=title, viral_title=title, niche_id=niche_id)

        chosen_base = None
        if background_image_path and os.path.exists(str(background_image_path)):
            chosen_base = str(background_image_path)
        elif video_path and os.path.exists(video_path):
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
