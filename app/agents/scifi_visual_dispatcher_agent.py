"""
app.agents.scifi_visual_dispatcher_agent — Tác nhân Phân Phối & Điều Phối Thị Giác Chuyên Biệt Cho Video Viễn Tưởng (Sci-Fi & Cosmic Visual Dispatcher Agent).

Nhiệm vụ cốt lõi:
1. Nhận diện chuyên sâu các yếu tố Viễn tưởng, Vũ trụ, Công nghệ tương lai, Kỳ ảo (Sci-Fi, Cosmic, Futuristic, Cyberpunk, Multiverse).
2. Triệt tiêu hoàn toàn tình trạng "thuần ảnh tĩnh chuyển cảnh qua lại" (Anti-Slideshow Manifesto) gây nhàm chán cho thể loại viễn tưởng.
3. Điều phối ưu tiên tối đa:
   - ƯU TIÊN 1: Sinh Video AI chuyển động điện ảnh thực sự (Text-to-Video / AI Video Generation).
   - ƯU TIÊN 2: Khai thác video clip 9:16 chuyển động 30fps/60fps có sẵn trong kho (Curated Stock & Archive Video Footage) bằng bộ từ khóa chuyên dụng.
   - HẠN CHẾ TỐI ĐA ẢNH TĨNH: Chỉ cho phép ảnh tĩnh như phương án dự phòng khẩn cấp cuối cùng (Zero-Failure Fallback) khi cả 2 nguồn video trên đều bất khả dụng, đồng thời bắt buộc kích hoạt hiệu ứng camera 3D Hyper-Warp/Cinematic Motion mạnh mẽ.
"""
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ── BỘ TỪ ĐIỂN NHẬN DIỆN YẾU TỐ VIỄN TƯỞNG & VŨ TRỤ CHUYÊN SÂU ──
SCIFI_GENRE_KEYWORDS: Dict[str, List[str]] = {
    "deep_space_cosmic": [
        "vũ trụ", "hố đen", "lỗ đen", "thiên hà", "ngân hà", "tinh vân", "ngôi sao", "hành tinh",
        "tiểu hành tinh", "siêu tân tinh", "sao neutron", "chân trời sự kiện", "vật chất tối",
        "năng lượng tối", "không thời gian", "bẻ cong không gian", "vụ nổ big bang", "phi hành gia",
        "black hole", "wormhole", "galaxy", "nebula", "supernova", "neutron star", "event horizon",
        "dark matter", "dark energy", "space-time", "cosmos", "universe", "deep space", "interstellar",
        "astronaut", "orbit", "exoplanet", "cosmic ray", "quasar", "accretion disk"
    ],
    "cyberpunk_futuristic": [
        "tương lai", "công nghệ cao", "thành phố tương lai", "cyberpunk", "người máy", "robot",
        "trí tuệ nhân tạo", "ai", "cyborg", "android", "máy học", "công nghệ lượng tử", "máy tính lượng tử",
        "hình chiếu 3d", "hologram", "chip não", "neuralink", "thực tế ảo", "vr", "matrix", "mạng lượng tử",
        "futuristic", "cyberpunk", "robot", "android", "cyborg", "artificial intelligence", "quantum computing",
        "hologram", "neural network", "simulation theory", "nanotechnology", "neon future", "flying car"
    ],
    "multiverse_time_travel": [
        "du hành thời gian", "cỗ máy thời gian", "nghịch lý thời gian", "thế giới song song", "đa vũ trụ",
        "chiều không gian thứ 4", "cổng không gian", "bước nhảy không gian", "vượt tốc độ ánh sáng",
        "lỗ sâu", "vũ trụ song song", "nghịch lý ông nội", "dòng thời gian",
        "time travel", "multiverse", "parallel universe", "wormhole", "warp speed", "hyperspace",
        "quantum realm", "4th dimension", "time paradox", "portal", "quantum entanglement", "dyson sphere"
    ],
    "alien_extraterrestrial": [
        "người ngoài hành tinh", "ufo", "đĩa bay", "văn minh ngoài trái đất", "sinh vật ngoài hành tinh",
        "phi thuyền", "tàu vũ trụ", "chiến hạm không gian", "sự sống ngoài trái đất", "thang kardashev",
        "văn minh loại 1", "văn minh loại 2", "văn minh loại 3", "tín hiệu vô tuyến vũ trụ", "nghịch lý fermi",
        "alien", "extraterrestrial", "ufo", "uap", "alien civilization", "alien planet", "spaceship",
        "starship", "mothership", "kardashev scale", "fermi paradox", "space anomaly", "alien contact"
    ],
    "general_scifi_fantasy": [
        "viễn tưởng", "khoa học viễn tưởng", "sci-fi", "scifi", "science fiction", "giả tưởng",
        "huyền bí", "kỳ ảo", "siêu nhiên", "siêu năng lực", "bảo bối tương lai", "vũ khí năng lượng",
        "laser", "chùm tia năng lượng", "vũ trụ quan", "thế kỷ 22", "thế kỷ 30", "năm 3000"
    ]
}

# ── KHO TỪ KHÓA TÌM VIDEO CHUYỂN ĐỘNG SẴN CÓ CHUẨN ĐIỆN ẢNH (CURATED STOCK VIDEO QUERIES) ──
CURATED_SCIFI_STOCK_QUERIES: Dict[str, List[str]] = {
    "deep_space_cosmic": [
        "black hole space",
        "galaxy stars cosmos",
        "deep space nebula",
        "planet in space",
        "cosmic energy motion",
        "universe stars travel",
        "space travel stars",
        "stars nebula motion",
        "earth from space"
    ],
    "cyberpunk_futuristic": [
        "cyberpunk city night neon",
        "futuristic technology motion",
        "futuristic robot ai",
        "hologram technology abstract",
        "neon lights motion",
        "future city traffic",
        "abstract digital technology"
    ],
    "multiverse_time_travel": [
        "warp speed tunnel stars",
        "quantum abstract glowing energy",
        "portal space time travel",
        "hyperspace light motion",
        "abstract energy tunnel",
        "wormhole space motion"
    ],
    "alien_extraterrestrial": [
        "spaceship traveling space",
        "alien planet landscape",
        "spacecraft flying stars",
        "deep cosmos mystery",
        "futuristic spaceship flight"
    ],
    "general_scifi_fantasy": [
        "sci fi energy motion",
        "space nebula motion",
        "futuristic digital motion",
        "abstract glowing portal",
        "cosmic light tunnel"
    ]
}


class SciFiVisualDispatcherAgent:
    """
    Tác nhân Phân Phối & Giám Sát Thị Giác Viễn Tưởng (Sci-Fi Visual Dispatcher Agent).
    Chịu trách nhiệm bảo đảm mọi video viễn tưởng luôn có chuyển động điện ảnh sống động,
    ưu tiên AI Video & Kho Video Clip 9:16 có sẵn, xóa bỏ hoàn toàn hiện tượng ảnh tĩnh lặp lại.
    """

    def __init__(self):
        pass

    def detect_scifi_elements(
        self,
        topic: str = "",
        narration: str = "",
        visual_keyword: str = "",
        visual_prompt: str = "",
        channel_niche: str = "",
    ) -> Tuple[bool, float, str, List[str]]:
        """
        Nhận diện xem phân cảnh hoặc chủ đề có yếu tố viễn tưởng / vũ trụ hay không.
        Trả về:
        - is_scifi (bool): Có yếu tố viễn tưởng hay không.
        - confidence (float): Độ tin cậy (0.0 - 1.0).
        - matched_subgenre (str): Phân nhánh cụ thể (deep_space_cosmic, cyberpunk, multiverse, alien, general).
        - matched_keywords (List[str]): Các từ khóa khớp.
        """
        full_text = f"{topic} {narration} {visual_keyword} {visual_prompt} {channel_niche}".lower()

        scores: Dict[str, int] = {}
        matched_kws: Dict[str, List[str]] = {}

        for subgenre, kw_list in SCIFI_GENRE_KEYWORDS.items():
            count = 0
            matched = []
            for kw in kw_list:
                # Tìm kiếm từ khóa chính xác
                if re.search(r'\b' + re.escape(kw) + r'\b', full_text) or kw in full_text:
                    count += 1
                    matched.append(kw)
            if count > 0:
                scores[subgenre] = count
                matched_kws[subgenre] = matched

        if not scores:
            return False, 0.0, "none", []

        best_subgenre = max(scores, key=scores.get)
        total_matches = sum(scores.values())
        confidence = min(1.0, 0.4 + (total_matches * 0.15))

        all_matched = [kw for kws in matched_kws.values() for kw in kws]
        is_scifi = confidence >= 0.5 or total_matches >= 2 or any(k in full_text for k in ["viễn tưởng", "sci-fi", "hố đen", "lỗ đen", "vũ trụ", "black hole", "galaxy", "alien", "multiverse"])

        return is_scifi, confidence, best_subgenre, all_matched

    def get_curated_video_search_queries(
        self,
        subgenre: str = "general_scifi_fantasy",
        visual_keyword: str = "",
        narration: str = "",
    ) -> List[str]:
        """
        Sinh ra danh sách 3-5 từ khóa tìm kiếm video kho (Pexels / Pixabay / NASA)
        được tối ưu hóa đặc biệt để luôn trả về video clip chuyển động 9:16 sống động,
        thay vì các câu mô tả văn vẻ không thể tìm thấy trên stock.
        """
        curated_list = list(CURATED_SCIFI_STOCK_QUERIES.get(subgenre, CURATED_SCIFI_STOCK_QUERIES["general_scifi_fantasy"]))

        # Nếu có từ khóa cụ thể từ kịch bản, làm sạch và chèn lên đầu
        if visual_keyword:
            clean_kw = " ".join([w for w in visual_keyword.lower().split() if len(w) > 2][:3])
            if clean_kw and clean_kw not in curated_list:
                curated_list.insert(0, clean_kw)

        # Trích xuất thêm các yếu tố chuyển động từ narration
        narr_lower = narration.lower()
        if "black hole" in narr_lower or "lỗ đen" in narr_lower or "hố đen" in narr_lower:
            curated_list.insert(0, "black hole space")
        elif "spaceship" in narr_lower or "phi thuyền" in narr_lower or "tàu vũ trụ" in narr_lower:
            curated_list.insert(0, "spaceship traveling space")
        elif "alien" in narr_lower or "ngoài hành tinh" in narr_lower:
            curated_list.insert(0, "alien planet space")
        elif "city" in narr_lower or "thành phố" in narr_lower or "tương lai" in narr_lower:
            curated_list.insert(0, "cyberpunk city night")

        # Khử trùng lặp và giữ lại tối đa 5 truy vấn chất lượng nhất
        seen = set()
        final_queries = []
        for q in curated_list:
            if q not in seen:
                seen.add(q)
                final_queries.append(q)
            if len(final_queries) >= 5:
                break

        return final_queries

    def create_cinematic_ai_video_prompt(
        self,
        prompt: str,
        subgenre: str = "general_scifi_fantasy",
        scene_id: int = 1,
    ) -> str:
        """
        Sinh câu lệnh Prompt điện ảnh 9:16 siêu thực dành riêng cho các mô hình AI Video
        (Google Veo 3.1, Wan 2.1, Kling, Luma, Pika), ép chuyển động mạnh mẽ (High Dynamic Motion).
        """
        clean_base = prompt.strip()
        motion_prefix = "Cinematic 9:16 vertical motion video, 8k resolution, award-winning sci-fi cinematography, fluid dynamic camera movement"

        if subgenre == "deep_space_cosmic":
            style_cues = "hyper-realistic cosmic phenomena, glowing volumetric accretion disk, warping space-time gravitational lensing, deep space dark aesthetic, photorealistic, 60fps fluid motion"
        elif subgenre == "cyberpunk_futuristic":
            style_cues = "hyper-detailed futuristic cyberpunk aesthetic, glowing neon reflections on wet surfaces, flying vehicles moving through skyscrapers, atmospheric volumetric fog, 60fps"
        elif subgenre == "multiverse_time_travel":
            style_cues = "hyperspace portal opening, reality bending, shimmering quantum energy waves, glowing particle acceleration, mind-bending dimensional warp, 60fps"
        elif subgenre == "alien_extraterrestrial":
            style_cues = "breathtaking alien planetary landscape, massive alien vessel hovering in skies, eerie bioluminescent atmosphere, cinematic scale, 60fps"
        else:
            style_cues = "epic visual effects, dramatic chiaroscuro lighting, intense motion, no blur, high definition 8k, 60fps"

        return f"{motion_prefix}, {clean_base}, {style_cues}, no text, no watermark, vertical 9:16 format"

    def dispatch_scene_strategy(
        self,
        scene: Dict[str, Any],
        topic: str = "",
        channel_niche: str = "",
        idx: int = 0,
        total_scenes: int = 1,
    ) -> Dict[str, Any]:
        """
        Phân phối và quyết định chiến lược thị giác tối ưu cho 1 phân cảnh cụ thể:
        - Nếu có yếu tố viễn tưởng:
          + Ép `production_method` thành `TEXT_TO_VIDEO` hoặc `STOCK_FOOTAGE` (Video clip 9:16).
          + Gắn cờ `reject_static_slideshow = True`.
          + Cung cấp danh sách `curated_search_queries` và `cinematic_video_prompt`.
        """
        narr = scene.get("narration") or ""
        kw = scene.get("visual_keyword") or ""
        vp = scene.get("visual_prompt") or ""
        user_pref = scene.get("media_type")

        is_scifi, confidence, subgenre, kws = self.detect_scifi_elements(
            topic=topic,
            narration=narr,
            visual_keyword=kw,
            visual_prompt=vp,
            channel_niche=channel_niche,
        )

        curated_queries = self.get_curated_video_search_queries(
            subgenre=subgenre,
            visual_keyword=kw,
            narration=narr,
        )
        ai_vid_prompt = self.create_cinematic_ai_video_prompt(
            prompt=vp or kw or narr[:100],
            subgenre=subgenre,
            scene_id=idx + 1,
        )

        # ── NẾU PHÁT HIỆN YẾU TỐ VIỄN TƯỞNG / VŨ TRỤ ──
        if is_scifi:
            logger.info(
                f"🚀 [SciFiDispatcher] Cảnh {idx+1}/{total_scenes} PHÁT HIỆN YẾU TỐ VIỄN TƯỞNG [{subgenre} | Conf: {confidence:.2f}]: "
                f"Từ khóa khớp: {kws[:4]}. KÍCH HOẠT CHẾ ĐỘ ƯU TIÊN FULL VIDEO (AI VIDEO & KHO VIDEO)!"
            )

            # Cảnh 1 (Hook) hoặc cảnh có từ khóa hành động -> STOCK_FOOTAGE (Video clip chuyển động thực tế từ kho)
            # Cảnh trừu tượng/vũ trụ vô tận -> TEXT_TO_VIDEO (AI Video)
            if idx == 0 or any(w in narr.lower() for w in ["hành động", "nhìn", "bước", "nhanh", "chạy", "tốc độ", "bay", "bùng nổ"]):
                chosen_method = "STOCK_FOOTAGE"
                chosen_tool = "pexels_video_api"
                rationale = f"Cảnh viễn tưởng có nhịp điệu động ({subgenre}) -> ưu tiên lấy Video Clip 9:16 chuyển động thực từ kho để giữ chân người xem."
            else:
                chosen_method = "TEXT_TO_VIDEO"
                chosen_tool = "wan_t2v"
                rationale = f"Cảnh viễn tưởng siêu thực ({subgenre}) -> ưu tiên AI Video Generator tạo hoạt cảnh động hoành tráng, không dùng ảnh tĩnh."

            # Nếu người dùng có chỉ định thủ công thì tôn trọng nhưng vẫn giữ cờ Sci-Fi
            if user_pref == "video":
                chosen_method = "STOCK_FOOTAGE"
            elif user_pref == "ai_video":
                chosen_method = "TEXT_TO_VIDEO"

            return {
                "is_scifi": True,
                "subgenre": subgenre,
                "confidence": confidence,
                "production_method": chosen_method,
                "tool": chosen_tool,
                "rationale": rationale,
                "reject_static_slideshow": True,
                "search_queries": curated_queries,
                "search_query": curated_queries[0] if curated_queries else kw,
                "ai_prompt": ai_vid_prompt,
                "camera_motion": "zoom_in" if idx % 2 == 0 else "pan_right",
                "sfx_cue": "whoosh" if idx == 0 else "tension_riser"
            }

        # ── NẾU KHÔNG PHẢI VIỄN TƯỞNG: Giữ nguyên cơ chế thông thường ──
        return {
            "is_scifi": False,
            "subgenre": "none",
            "confidence": 0.0,
            "production_method": "AUTO",
            "tool": "auto",
            "rationale": "Chủ đề đời thực / thường nhật.",
            "reject_static_slideshow": False,
            "search_queries": [kw] if kw else [],
            "search_query": kw,
            "ai_prompt": vp,
            "camera_motion": "zoom_in",
            "sfx_cue": "whoosh"
        }

    def enforce_scifi_video_plan(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        channel_niche: str = "",
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Kiểm tra toàn bộ kế hoạch video. Nếu video mang bản chất Viễn tưởng:
        - Tự động nâng cấp toàn bộ danh sách phân cảnh sang chế độ Ưu Tiên Video Động.
        - Đảm bảo 100% các phân cảnh đều được cung cấp bộ từ khóa kho video chuyên dụng + AI prompt.
        """
        is_topic_scifi, conf, main_subgenre, _ = self.detect_scifi_elements(
            topic=topic,
            channel_niche=channel_niche,
        )

        scifi_scene_count = 0
        dispatched_scenes = []

        for idx, sc in enumerate(scenes):
            d_res = self.dispatch_scene_strategy(
                scene=sc,
                topic=topic,
                channel_niche=channel_niche,
                idx=idx,
                total_scenes=len(scenes),
            )
            if d_res["is_scifi"] or is_topic_scifi:
                scifi_scene_count += 1
                sc_copy = dict(sc)
                sc_copy["production_method"] = d_res["production_method"]
                sc_copy["tool"] = d_res["tool"]
                sc_copy["search_query"] = d_res["search_query"]
                sc_copy["search_queries"] = d_res["search_queries"]
                sc_copy["ai_prompt"] = d_res["ai_prompt"]
                sc_copy["strategy_rationale"] = d_res["rationale"]
                sc_copy["reject_static_slideshow"] = True
                sc_copy["is_scifi"] = True
                sc_copy["subgenre"] = d_res.get("subgenre", main_subgenre)
                dispatched_scenes.append(sc_copy)
            else:
                dispatched_scenes.append(sc)

        overall_scifi = is_topic_scifi or (scifi_scene_count >= max(1, len(scenes) // 2))
        if overall_scifi:
            logger.info(f"🌌 [SciFiDispatcher] TOÀN BỘ VIDEO ĐƯỢC XÁC ĐỊNH LÀ THỂ LOẠI VIỄN TƯỞNG ({scifi_scene_count}/{len(scenes)} cảnh). Áp dụng quy chế Chống Slideshow Ảnh Tĩnh 100%!")

        return overall_scifi, dispatched_scenes
