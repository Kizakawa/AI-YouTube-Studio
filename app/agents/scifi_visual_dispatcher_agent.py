"""
app.agents.scifi_visual_dispatcher_agent — Tác nhân Phân Phối & Điều Phối Thị Giác Chuyên Biệt Cho Video Viễn Tưởng (Sci-Fi & Cosmic Visual Dispatcher Agent).

Nhiệm vụ cốt lõi:
1. Nhận diện các yếu tố Viễn tưởng, Vũ trụ, Công nghệ tương lai, Kỳ ảo khi cần thiết.
2. Luôn ưu tiên giữ nguyên truy vấn ngữ cảnh cụ thể (`visual_keyword` / `stock_query`) do LLM sinh ra từ kịch bản của người dùng,
   chỉ bổ sung truy vấn dự phòng khi cảnh thiếu từ khóa cụ thể.
"""
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

SCIFI_GENRE_KEYWORDS: Dict[str, List[str]] = {
    "deep_space_cosmic": [
        "vũ trụ", "hố đen", "lỗ đen", "thiên hà", "ngân hà", "tinh vân", "siêu tân tinh", "sao neutron",
        "chân trời sự kiện", "vật chất tối", "năng lượng tối", "không thời gian", "vụ nổ big bang",
        "black hole", "wormhole", "galaxy", "nebula", "supernova", "neutron star", "event horizon",
        "dark matter", "dark energy", "space-time", "cosmos", "deep space", "interstellar",
        "exoplanet", "quasar", "pulsar", "accretion disk", "dyson sphere", "white hole",
    ],
    "cyberpunk_futuristic": [
        "thành phố tương lai", "cyberpunk", "cyborg", "android", "công nghệ lượng tử", "máy tính lượng tử",
        "hologram", "neuralink", "siêu trí tuệ", "robot hình người", "vũ khí năng lượng", "xe bay",
        "futuristic", "quantum computing", "simulation theory", "nanotechnology", "neon future",
        "flying car", "exoskeleton", "singularity", "superintelligence", "synthetic human", "humanoid",
    ],
    "multiverse_time_travel": [
        "du hành thời gian", "cỗ máy thời gian", "nghịch lý thời gian", "thế giới song song", "đa vũ trụ",
        "chiều không gian thứ 4", "cổng không gian", "bước nhảy không gian", "lỗ sâu", "vũ trụ song song",
        "time travel", "multiverse", "parallel universe", "warp speed", "hyperspace",
        "quantum realm", "4th dimension", "time paradox", "quantum entanglement", "higher dimension",
    ],
    "alien_extraterrestrial": [
        "người ngoài hành tinh", "ufo", "uap", "đĩa bay", "văn minh ngoài trái đất", "sinh vật ngoài hành tinh",
        "phi thuyền", "tàu vũ trụ", "chiến hạm không gian", "thang kardashev", "nghịch lý fermi",
        "alien", "extraterrestrial", "alien civilization", "alien planet",
        "spaceship", "starship", "mothership", "kardashev scale", "fermi paradox", "great filter",
    ],
    "general_scifi_fantasy": [
        "viễn tưởng", "khoa học viễn tưởng", "sci-fi", "scifi", "science fiction",
        "thế kỷ 22", "thế kỷ 30", "năm 3000",
    ],
}

CURATED_SCIFI_STOCK_QUERIES: Dict[str, List[str]] = {
    "deep_space_cosmic": [
        "black hole space accretion disk",
        "deep space galaxy nebula vertical",
        "stars space travel flying",
        "earth from orbit space station",
        "supernova cosmic explosion stars",
    ],
    "cyberpunk_futuristic": [
        "cyberpunk city night neon vertical",
        "futuristic neon traffic metropolis",
        "hologram user interface hud digital",
        "robot humanoid cyborg vertical",
        "artificial intelligence data glowing network",
    ],
    "multiverse_time_travel": [
        "hyperspace warp tunnel stars",
        "wormhole space time portal",
        "quantum glowing energy particles",
        "abstract light speed tunnel vertical",
    ],
    "alien_extraterrestrial": [
        "spaceship flying through space",
        "alien planet surface landscape",
        "futuristic spacecraft landing",
        "deep cosmos space anomaly",
    ],
    "general_scifi_fantasy": [
        "sci fi energy motion 4k",
        "space nebula motion vertical",
        "futuristic digital motion",
        "cosmic light tunnel travel",
    ],
}


class SciFiVisualDispatcherAgent:
    """
    Tác nhân Phân Phối & Giám Sát Thị Giác Viễn Tưởng (Sci-Fi Visual Dispatcher Agent).
    Bảo toàn truy vấn ngữ cảnh của từng phân cảnh do LLM sinh ra và bổ sung truy vấn dự phòng cùng subgenre.
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
        raw_text = f"{topic} {narration} {visual_keyword} {visual_prompt} {channel_niche}"
        full_text = raw_text.lower()

        scores: Dict[str, int] = {}
        matched_kws: Dict[str, List[str]] = {}

        def _has(term: str) -> bool:
            return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", full_text) is not None

        for subgenre, kw_list in SCIFI_GENRE_KEYWORDS.items():
            count = 0
            matched = []
            for kw in kw_list:
                if _has(kw):
                    count += 1
                    matched.append(kw)
            if subgenre == "cyberpunk_futuristic":
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
        is_scifi = confidence >= 0.5 or total_matches >= 2 or any(
            _has(k) for k in ["viễn tưởng", "sci-fi", "hố đen", "lỗ đen", "vũ trụ", "black hole", "galaxy", "alien", "multiverse"]
        )

        return is_scifi, confidence, best_subgenre, all_matched

    def get_curated_video_search_queries(
        self,
        subgenre: str = "general_scifi_fantasy",
        visual_keyword: str = "",
        narration: str = "",
    ) -> List[str]:
        """
        Ưu tiên tuyệt đối từ khóa cụ thể của phân cảnh (`visual_keyword`) đứng đầu danh sách,
        sau đó mới bổ sung các truy vấn dự phòng theo phân nhánh `subgenre`.
        """
        final_queries: List[str] = []
        seen = set()

        def _push(q: str) -> None:
            q_clean = " ".join((q or "").strip().split())
            if q_clean and q_clean.lower() not in seen:
                seen.add(q_clean.lower())
                final_queries.append(q_clean)

        if visual_keyword:
            _push(" ".join([w for w in visual_keyword.strip().split() if len(w) > 1][:5]))
            _push(" ".join([w for w in visual_keyword.strip().split() if len(w) > 2][:3]))

        for q in CURATED_SCIFI_STOCK_QUERIES.get(subgenre, CURATED_SCIFI_STOCK_QUERIES["general_scifi_fantasy"]):
            _push(q)
            if len(final_queries) >= 5:
                break

        return final_queries[:5]

    def create_cinematic_ai_video_prompt(
        self,
        prompt: str,
        subgenre: str = "general_scifi_fantasy",
        scene_id: int = 1,
    ) -> str:
        clean_base = prompt.strip()
        motion_prefix = (
            "Cinematic 9:16 vertical motion video, 8k resolution, award-winning sci-fi cinematography, "
            "fluid dynamic camera movement, Unreal Engine 5 Octane render"
        )

        style_map = {
            "deep_space_cosmic": "hyper-realistic cosmic space-time phenomena, glowing nebula dust, deep space dark void contrast, fluid dynamic 60fps camera drift, IMAX 70mm",
            "cyberpunk_futuristic": "hyper-detailed futuristic metropolis, glowing cyan and magenta neon reflections, atmospheric volumetric fog, ray-traced reflections, 60fps fluid motion",
            "multiverse_time_travel": "hyperspace dimensional warp, shimmering quantum energy waves, glowing particle acceleration, breathtaking cinematic VFX, 60fps",
            "alien_extraterrestrial": "breathtaking extraterrestrial landscape, eerie bioluminescent atmosphere, cinematic scale, 60fps fluid motion",
        }
        style_cues = style_map.get(
            subgenre,
            "epic visual effects, dramatic volumetric lighting, intense motion, razor-sharp focus, high definition 8k, 60fps",
        )
        return f"{motion_prefix}, {clean_base}, {style_cues}, no text, no watermark, vertical 9:16 format"

    def dispatch_scene_strategy(
        self,
        scene: Dict[str, Any],
        topic: str = "",
        channel_niche: str = "",
        idx: int = 0,
        total_scenes: int = 1,
    ) -> Dict[str, Any]:
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

        if is_scifi:
            from app.agents.visual_tool_router import VisualToolRouter
            route = VisualToolRouter().route_scene(scene, topic=topic, subgenre_queries=curated_queries)
            chosen_method = route["production_method"]
            chosen_tool = route["tool"]
            rationale = f"Cảnh viễn tưởng ({subgenre}) {route['rationale']}"
            prefer_generation = route["prefer_generation"]
            routed_queries = route["search_queries"] or curated_queries

            if user_pref == "video":
                chosen_method = "STOCK_FOOTAGE"
                prefer_generation = False
            elif user_pref == "ai_video":
                chosen_method = "TEXT_TO_VIDEO"
                prefer_generation = True

            scifi_motions = ["hyper_zoom", "cinematic_drift", "zoom_out", "warp_zoom", "pan_left", "pan_right"]
            chosen_motion = "hyper_zoom" if idx == 0 else scifi_motions[idx % len(scifi_motions)]

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
            "sfx_cue": "whoosh",
        }

    def enforce_scifi_video_plan(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        channel_niche: str = "",
    ) -> Tuple[bool, List[Dict[str, Any]]]:
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
        return overall_scifi, dispatched_scenes
