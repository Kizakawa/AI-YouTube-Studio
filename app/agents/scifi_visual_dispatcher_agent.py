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
        "hố trắng", "sao lùn", "hệ mặt trời", "sóng hấp dẫn", "vũ trụ vô tận", "kính viễn vọng",
        "black hole", "wormhole", "galaxy", "nebula", "supernova", "neutron star", "event horizon",
        "dark matter", "dark energy", "space-time", "cosmos", "universe", "deep space", "interstellar",
        "astronaut", "orbit", "exoplanet", "cosmic ray", "quasar", "pulsar", "accretion disk",
        "gravitational lensing", "dyson sphere", "white hole", "james webb"
    ],
    "cyberpunk_futuristic": [
        "tương lai", "công nghệ cao", "thành phố tương lai", "cyberpunk", "người máy", "robot",
        "trí tuệ nhân tạo", "agi", "cyborg", "android", "máy học", "công nghệ lượng tử", "máy tính lượng tử",
        "hình chiếu 3d", "hologram", "chip não", "neuralink", "thực tế ảo", "matrix", "mạng lượng tử",
        "siêu trí tuệ", "robot hình người", "vũ khí năng lượng", "xe bay", "công nghệ nano",
        "futuristic", "cyberpunk", "robot", "android", "cyborg", "artificial intelligence", "quantum computing",
        "hologram", "neural network", "simulation theory", "nanotechnology", "neon future", "flying car",
        "exoskeleton", "singularity", "superintelligence", "synthetic human", "humanoid"
    ],
    "multiverse_time_travel": [
        "du hành thời gian", "cỗ máy thời gian", "nghịch lý thời gian", "thế giới song song", "đa vũ trụ",
        "chiều không gian thứ 4", "cổng không gian", "bước nhảy không gian", "vượt tốc độ ánh sáng",
        "lỗ sâu", "vũ trụ song song", "nghịch lý ông nội", "dòng thời gian", "bước nhảy lượng tử", "rối lượng tử",
        "time travel", "multiverse", "parallel universe", "wormhole", "warp speed", "hyperspace",
        "quantum realm", "4th dimension", "time paradox", "portal", "quantum entanglement", "higher dimension", "timeline"
    ],
    "alien_extraterrestrial": [
        "người ngoài hành tinh", "ufo", "uap", "đĩa bay", "văn minh ngoài trái đất", "sinh vật ngoài hành tinh",
        "phi thuyền", "tàu vũ trụ", "chiến hạm không gian", "sự sống ngoài trái đất", "thang kardashev",
        "văn minh loại 1", "văn minh loại 2", "văn minh loại 3", "tín hiệu vô tuyến vũ trụ", "nghịch lý fermi",
        "bộ lọc vĩ đại", "sao hỏa", "alien", "extraterrestrial", "ufo", "uap", "alien civilization", "alien planet",
        "spaceship", "starship", "mothership", "kardashev scale", "fermi paradox", "space anomaly", "alien contact", "great filter"
    ],
    "general_scifi_fantasy": [
        "viễn tưởng", "khoa học viễn tưởng", "sci-fi", "scifi", "science fiction", "giả tưởng",
        "huyền bí", "kỳ ảo", "siêu nhiên", "siêu năng lực", "bảo bối tương lai", "vũ khí năng lượng",
        "laser", "chùm tia năng lượng", "vũ trụ quan", "thế kỷ 22", "thế kỷ 30", "năm 3000", "thế giới ảo"
    ]
}

# ── KHO TỪ KHÓA TÌM VIDEO CHUYỂN ĐỘNG SẴN CÓ CHUẨN ĐIỆN ẢNH (CURATED STOCK VIDEO QUERIES) ──
CURATED_SCIFI_STOCK_QUERIES: Dict[str, List[str]] = {
    "deep_space_cosmic": [
        "black hole space accretion disk",
        "deep space galaxy nebula vertical",
        "stars space travel flying",
        "earth from orbit space station",
        "supernova cosmic explosion stars",
        "planet rings deep space motion",
        "cosmic energy glowing stars"
    ],
    "cyberpunk_futuristic": [
        "cyberpunk city night neon vertical",
        "futuristic neon traffic metropolis",
        "hologram user interface hud digital",
        "robot humanoid cyborg vertical",
        "artificial intelligence data glowing network",
        "futuristic laboratory sci fi technology",
        "neon rain reflections street"
    ],
    "multiverse_time_travel": [
        "hyperspace warp tunnel stars",
        "wormhole space time portal",
        "quantum glowing energy particles",
        "abstract light speed tunnel vertical",
        "portal dimension energy swirl",
        "time travel speed light motion"
    ],
    "alien_extraterrestrial": [
        "spaceship flying through space",
        "alien planet surface landscape",
        "futuristic spacecraft landing",
        "deep cosmos space anomaly",
        "alien glowing bioluminescent landscape",
        "ufo flying night sky mysterious"
    ],
    "general_scifi_fantasy": [
        "sci fi energy motion 4k",
        "space nebula motion vertical",
        "futuristic digital motion",
        "abstract glowing portal",
        "cosmic light tunnel travel"
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
        raw_text = f"{topic} {narration} {visual_keyword} {visual_prompt} {channel_niche}"
        full_text = raw_text.lower()

        scores: Dict[str, int] = {}
        matched_kws: Dict[str, List[str]] = {}

        def _has(term: str) -> bool:
            # So khớp NGUYÊN TỪ (hỗ trợ tiếng Việt có dấu). Trước đây dùng thêm `kw in full_text` nên
            # 'ai' khớp trong 'brain' / 'said', 'vr' trong 'every' -> video tâm lý bị xếp nhầm là cyberpunk.
            return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", full_text) is not None

        for subgenre, kw_list in SCIFI_GENRE_KEYWORDS.items():
            count = 0
            matched = []
            for kw in kw_list:
                if _has(kw):
                    count += 1
                    matched.append(kw)
            if subgenre == "cyberpunk_futuristic":
                # 'AI' / 'VR' chỉ tính khi VIẾT HOA: 'Ai Cập', 'ai đó', 'every' không phải công nghệ
                for acr in ("AI", "VR"):
                    if re.search(rf"(?<![\w]){acr}(?![\w])(?!\s+c[ậa]p\b)", raw_text, flags=0) and not re.search(r"\bAI\s+C[ẬA]P\b", raw_text, re.IGNORECASE):
                        count += 1
                        matched.append(acr.lower())
            if count > 0:
                scores[subgenre] = count
                matched_kws[subgenre] = matched

        if not scores:
            return False, 0.0, "none", []

        best_subgenre = max(scores, key=scores.get)
        total_matches = sum(scores.values())
        confidence = min(1.0, 0.4 + (total_matches * 0.15))

        all_matched = [kw for kws in matched_kws.values() for kw in kws]
        is_scifi = confidence >= 0.5 or total_matches >= 2 or any(_has(k) for k in ["viễn tưởng", "sci-fi", "hố đen", "lỗ đen", "vũ trụ", "black hole", "galaxy", "alien", "multiverse"])

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
        (Google Veo 3.1, Wan 2.1, Kling, Luma, Pika), ép chuyển động mạnh mẽ (High Dynamic Motion),
        Unreal Engine 5 Octane render và ánh sáng tương phản rực rỡ.
        """
        clean_base = prompt.strip()
        motion_prefix = "Cinematic 9:16 vertical motion video, 8k resolution, award-winning sci-fi cinematography, fluid dynamic camera movement, Unreal Engine 5 Octane render"

        if subgenre == "deep_space_cosmic":
            style_cues = "hyper-realistic black hole accretion disk with intense gravitational lensing, warping space-time, glowing cosmic gas and brilliant nebula dust, deep space dark void contrast, fluid dynamic 60fps camera drift, IMAX 70mm"
        elif subgenre == "cyberpunk_futuristic":
            style_cues = "hyper-detailed futuristic cyberpunk metropolis, glowing cyan and magenta neon reflections on wet streets, flying vehicles moving through skyscrapers, atmospheric volumetric fog, ray-traced reflections, 60fps fluid motion"
        elif subgenre == "multiverse_time_travel":
            style_cues = "hyperspace portal opening, reality bending, shimmering quantum energy waves, glowing hyper-speed particle acceleration, mind-bending dimensional warp, breathtaking cinematic VFX, 60fps"
        elif subgenre == "alien_extraterrestrial":
            style_cues = "breathtaking alien planetary landscape, massive alien vessel hovering in skies, eerie bioluminescent atmosphere, twin moons in sky, cinematic scale, 60fps fluid motion"
        else:
            style_cues = "epic visual effects, dramatic volumetric lighting, vivid neon reflections, intense motion, razor-sharp focus, high definition 8k, 60fps"

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

            # Quyết định công cụ theo BẢN CHẤT cảnh (VisualToolRouter) thay vì vị trí cảnh / từ hành động:
            # - Thứ có thật đã được quay (phi hành gia, tên lửa...)      -> kho video thật (NASA/Pexels)
            # - Có thật nhưng không quay được (hố đen, siêu tân tinh...) -> kho mô phỏng CGI, AI dự phòng
            # - Hư cấu hoàn toàn (người ngoài hành tinh, quả cầu Dyson)  -> sinh bằng AI
            # - Khái niệm trừu tượng (thuyết mô phỏng, vô hạn)           -> motion graphics từ kho
            from app.agents.visual_tool_router import VisualToolRouter
            route = VisualToolRouter().route_scene(scene, topic=topic, subgenre_queries=curated_queries)
            chosen_method = route["production_method"]
            chosen_tool = route["tool"]
            rationale = f"Cảnh viễn tưởng ({subgenre}) {route['rationale']}"
            prefer_generation = route["prefer_generation"]
            routed_queries = route["search_queries"] or curated_queries

            # Nếu người dùng có chỉ định thủ công thì tôn trọng nhưng vẫn giữ cờ Sci-Fi
            if user_pref == "video":
                chosen_method = "STOCK_FOOTAGE"
                prefer_generation = False
            elif user_pref == "ai_video":
                chosen_method = "TEXT_TO_VIDEO"
                prefer_generation = True
            logger.info(f"🧭 [SciFiDispatcher] Cảnh {idx+1}: {chosen_method} — {rationale}")

            # Chuỗi chuyển động camera viễn tưởng đa dạng
            scifi_motions = ["hyper_zoom", "cinematic_drift", "zoom_out", "warp_zoom", "pan_left", "pan_right"]
            chosen_motion = "hyper_zoom" if idx == 0 else scifi_motions[idx % len(scifi_motions)]

            # Bản đồ SFX thực tế từ assets/sfx/ (hit, impact_sub, whoosh_fast, glitch, reveal, riser, sub_drop)
            scifi_sfx_catalog = {
                "deep_space_cosmic": ["impact_sub", "whoosh_fast", "sub_drop", "reveal", "riser", "whoosh_heavy"],
                "cyberpunk_futuristic": ["impact_sub", "glitch", "whoosh_fast", "sub_drop", "riser", "glitch"],
                "multiverse_time_travel": ["impact_sub", "glitch", "whoosh_fast", "sub_drop", "riser", "whoosh_heavy"],
                "alien_extraterrestrial": ["impact_sub", "reveal", "whoosh_heavy", "sub_drop", "riser", "impact_sub"],
                "general_scifi_fantasy": ["impact_sub", "whoosh_fast", "glitch", "reveal", "riser", "whoosh_heavy"],
            }
            sfx_seq = scifi_sfx_catalog.get(subgenre, scifi_sfx_catalog["general_scifi_fantasy"])
            chosen_sfx = "impact_sub" if idx == 0 else sfx_seq[idx % len(sfx_seq)]

            return {
                "is_scifi": True,
                "subgenre": subgenre,
                "confidence": confidence,
                "production_method": chosen_method,
                "tool": chosen_tool,
                "rationale": rationale,
                "reject_static_slideshow": True,
                "search_queries": routed_queries,
                "search_query": routed_queries[0] if routed_queries else kw,
                "ai_prompt": ai_vid_prompt,
                "camera_motion": chosen_motion,
                "sfx_cue": chosen_sfx,
                "prefer_generation": prefer_generation,
                "reality_class": route["reality_class"],
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
                sc_copy["camera_motion"] = d_res.get("camera_motion", "zoom_in")
                sc_copy["sfx_cue"] = d_res.get("sfx_cue", "whoosh_fast")
                sc_copy["prefer_generation"] = d_res.get("prefer_generation", False)
                sc_copy["reality_class"] = d_res.get("reality_class", "")
                dispatched_scenes.append(sc_copy)
            else:
                dispatched_scenes.append(sc)

        overall_scifi = is_topic_scifi or (scifi_scene_count >= max(1, len(scenes) // 2))
        if overall_scifi:
            logger.info(f"🌌 [SciFiDispatcher] TOÀN BỘ VIDEO ĐƯỢC XÁC ĐỊNH LÀ THỂ LOẠI VIỄN TƯỞNG ({scifi_scene_count}/{len(scenes)} cảnh). Áp dụng quy chế Chống Slideshow Ảnh Tĩnh 100%!")

        return overall_scifi, dispatched_scenes
