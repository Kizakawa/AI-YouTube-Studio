"""
app.agents.video_strategist_agent — Tác nhân Đề Xuất Chiến Lược & Phương Pháp Sản Xuất Video (Video Production Strategist Agent).

Nhiệm vụ cốt lõi:
Phân tích toàn diện kịch bản, chủ đề, nhân vật và thể loại kênh để lập Bản Đề Xuất Kỹ Thuật Làm Video (Video Production Plan).
Chỉ định chính xác cho từng phân cảnh:
1. Phương pháp sản xuất tối ưu (production_method):
   - REAL_ARCHIVE   : Ảnh / tư liệu lưu trữ có thật 100% (nhân vật, di tích, sự kiện lịch sử).
   - IMAGE_TO_VIDEO : Sinh ảnh chủ đề 8K chuẩn trước, sau đó tạo video chuyển động phụ thuộc vào ảnh (để không méo mặt, chuẩn chi tiết).
   - TEXT_TO_VIDEO  : Sinh video AI trực tiếp (cho cảnh huyền bí, vũ trụ trừu tượng, năng lượng siêu thực).
   - STOCK_FOOTAGE  : Video clip B-roll chuyển động thực tế 9:16 (hành động đời thường, giữ chân người xem).
2. Công cụ thực thi (tool_selection):
   - wikimedia_archive / wikipedia
   - imagen3_i2v (ImageAgent + Cinematic Motion Engine)
   - wan_t2v (Generative Video AI)
   - pexels_video_api
3. Lời giải thích chiến lược (rationale):
   - Giải thích cặn kẽ tại sao cảnh này lại làm theo cách này để người dùng và hệ thống hiểu rõ.
"""
import json
import logging
from typing import Dict, Any, List, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


class VideoStrategistAgent:
    """
    Tác nhân Đề Xuất Chiến Lược & Phương Pháp Làm Video (Video Production Strategist).
    Đóng vai trò Tổng Đạo Diễn Hình Ảnh (Director of Photography) trong tiền kỳ sản xuất.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini(self, prompt: str, timeout: int = 20) -> Optional[str]:
        """Gọi Gemini REST API với KeyRotator và timeout an toàn."""
        if not self.rotator:
            return None

        import requests
        total_keys = max(len(self.rotator.keys), 1)

        for attempt in range(total_keys * 2):
            key = self.rotator.get_key()
            if not key:
                return None

            key_failed = False
            for model in AVAILABLE_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.35,
                        "responseMimeType": "application/json",
                    }
                }
                try:
                    res = requests.post(url, json=payload, timeout=timeout)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            return text
                    elif res.status_code in (429, 403):
                        self.rotator.rotate_key()
                        key_failed = True
                        break
                except Exception as e:
                    logger.debug(f"[VideoStrategist] Gemini call error on {model}: {e}")
                    continue

            if not key_failed:
                self.rotator.rotate_key()

        return None

    def propose_production_strategy(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        channel_niche: str = "general",
    ) -> Dict[str, Any]:
        """
        Lập bản đề xuất chiến lược kỹ thuật sản xuất video cho từng cảnh.
        Tích hợp SciFiVisualDispatcherAgent:
        - Nhận diện chuyên sâu yếu tố Viễn tưởng / Vũ trụ / Cyberpunk / Đa vũ trụ.
        - Ép phương thức sản xuất thành TEXT_TO_VIDEO (AI Video) hoặc STOCK_FOOTAGE (Kho video 9:16).
        - Tuyệt đối ngăn chặn hiện tượng tạo mỗi ảnh tĩnh chuyển qua lại gây nhàm chán!
        """
        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        scifi_agent = SciFiVisualDispatcherAgent()
        is_scifi_topic, conf, subgenre, scifi_kws = scifi_agent.detect_scifi_elements(
            topic=topic,
            channel_niche=channel_niche,
        )

        if is_scifi_topic:
            logger.info(
                f"🚀 [VideoStrategist] PHÁT HIỆN CHỦ ĐỀ VIỄN TƯỞNG ({subgenre}, Độ tin cậy: {conf:.2f}). "
                f"KÍCH HOẠT CHẾ ĐỘ ĐIỀU PHỐI ĐỘNG (ƯU TIÊN AI VIDEO & KHO VIDEO 9:16, CHỐNG SLIDESHOW ẢNH TĨNH)!"
            )

        # 1. Thử gọi AI Strategist
        ai_proposal = self._propose_with_ai(topic, scenes, primary_entity, entity_type, channel_niche)
        if ai_proposal and "scene_proposals" in ai_proposal and len(ai_proposal["scene_proposals"]) == len(scenes):
            logger.info("🎬 [VideoStrategist] ✅ Đã phê duyệt Bản Đề Xuất Chiến Lược Sản Xuất Video từ AI Strategist!")
            final_plan = ai_proposal
            plan_source = "ai"
        else:
            # 2. Heuristic Rules Fallback
            logger.info("🎬 [VideoStrategist] Kích hoạt Heuristic Rule-Based Strategist Fallback...")
            final_plan = self._propose_with_heuristics(topic, scenes, primary_entity, entity_type, channel_niche)
            plan_source = "heuristic"

        # 3. HẬU KIỂM ĐỊNH TUYẾN CÔNG CỤ (VisualToolRouter)
        # - Kế hoạch từ AI Strategist (Gemini): AI là người QUYẾT ĐỊNH. Router chỉ sửa khi AI chọn công cụ mà máy chủ
        #   KHÔNG CÓ thật (vd TEXT_TO_VIDEO khi chưa bật Veo/Wan) và bổ sung từ khóa khi AI trả từ khóa chung chung.
        #   Luật từ khóa KHÔNG được ghi đè phán đoán ngữ nghĩa của AI nữa (trước đây 'ai' trong 'Brain' đủ để ghi đè).
        # - Kế hoạch heuristic (Gemini lỗi / hết quota): router quyết định cho chủ đề viễn tưởng.
        if final_plan:
            final_plan["plan_source"] = plan_source
            self._apply_tool_routing(
                final_plan, scenes, topic, channel_niche, plan_source,
                is_scifi_topic, subgenre, scifi_agent,
            )
            if is_scifi_topic:
                final_plan["is_scifi_video"] = True
                final_plan["scifi_subgenre"] = subgenre

        # 4. CHỦ ĐỀ THIÊN NHIÊN / ĐỊA DANH CÓ THẬT -> ƯU TIÊN FOOTAGE THẬT
        # Ảnh AI miễn phí vẽ sai loài và không có bối cảnh thật (video cá sấu Amazon ra gấu / báo / người đứng trên nước).
        # Cảnh AI được chuyển sang STOCK_FOOTAGE; nếu kho stock không có clip đạt chuẩn, pipeline vẫn tự rơi về AI.
        if final_plan and not is_scifi_topic:
            try:
                from app.services.media.query_broadener import is_real_world_nature_topic
                # Chỉ xét CHỦ ĐỀ: lời thoại hay có ẩn dụ ("corporate jungle") dễ kích hoạt nhầm cho video tâm lý.
                if is_real_world_nature_topic(topic):
                    switched = 0
                    scene_by_id = {s.get("scene_id"): s for s in scenes}
                    for key in ("scenes_strategy", "scene_proposals"):
                        for strat in final_plan.get(key) or []:
                            if strat.get("production_method") in ("IMAGE_TO_VIDEO", "TEXT_TO_VIDEO"):
                                sc = scene_by_id.get(strat.get("scene_id"), {})
                                strat["production_method"] = "STOCK_FOOTAGE"
                                strat["strategy"] = "STOCK_VIDEO"
                                strat["tool"] = "pexels_video_api"
                                if sc.get("stock_query") and strat.get("search_query") in (None, "", "cinematic"):
                                    strat["search_query"] = sc["stock_query"]
                                strat["rationale"] = "Chủ đề thiên nhiên có thật: ưu tiên footage thật, AI chỉ là dự phòng."
                                if key == "scenes_strategy":
                                    switched += 1
                    if switched:
                        final_plan["real_footage_priority"] = True
                        logger.info(f"🌿 [VideoStrategist] Chủ đề thiên nhiên có thật -> chuyển {switched} cảnh AI sang footage thật (AI là dự phòng).")
            except Exception as e:
                logger.debug(f"[VideoStrategist] Nature real-footage enforcement error: {e}")

        return final_plan

    # Từ khóa quá chung chung, tìm kho sẽ ra clip ngẫu nhiên
    _GENERIC_QUERIES = {"", "cinematic", "cinematic shot", "b roll", "broll", "video", "footage", "scene", "background"}

    def _apply_tool_routing(
        self,
        final_plan: Dict[str, Any],
        scenes: List[Dict[str, Any]],
        topic: str,
        channel_niche: str,
        plan_source: str,
        is_scifi_topic: bool,
        subgenre: str,
        scifi_agent: Any,
    ) -> None:
        from app.agents.visual_tool_router import (
            VisualToolRouter, PURE_FICTION, REAL_FILMABLE, DEEP_SEA_MYSTERY, REALITY_KEYWORDS,
        )
        router = VisualToolRouter()
        true_t2v = router.caps.get("true_text_to_video", False)
        valid_methods = ("REAL_ARCHIVE", "IMAGE_TO_VIDEO", "TEXT_TO_VIDEO", "STOCK_FOOTAGE")

        prop_by_id = {p.get("scene_id"): p for p in final_plan.get("scene_proposals") or []}
        strat_by_id = {s.get("scene_id"): s for s in final_plan.get("scenes_strategy") or []}
        stats = {"kept_ai": 0, "capability_fix": 0, "router": 0}

        for idx, sc in enumerate(scenes):
            sid = sc.get("scene_id")
            targets = [d for d in (prop_by_id.get(sid), strat_by_id.get(sid)) if d is not None]
            if not targets:
                continue

            d_meta = None
            if is_scifi_topic:
                d_meta = scifi_agent.dispatch_scene_strategy(
                    scene=sc, topic=topic, channel_niche=channel_niche, idx=idx, total_scenes=len(scenes),
                )
            route = router.route_scene(sc, topic=topic, subgenre_queries=(d_meta or {}).get("search_queries"))

            # Bản chất cảnh: tin phân loại ngữ nghĩa của AI trước, từ khóa chỉ là dự phòng
            ai_reality = str(targets[0].get("reality_class") or "").upper()
            reality = ai_reality if ai_reality in REALITY_KEYWORDS else route["reality_class"]

            for t in targets:
                method = t.get("production_method")
                note = ""
                if plan_source == "ai" and method in valid_methods:
                    new_method = method
                    if method == "TEXT_TO_VIDEO" and not true_t2v:
                        # AI muốn video AI thật nhưng máy chủ chưa có -> chọn phương án thật sự tốt nhất hiện có
                        new_method = "IMAGE_TO_VIDEO" if reality == PURE_FICTION else "STOCK_FOOTAGE"
                        note = (f" [Hiệu chỉnh năng lực: AI chọn TEXT_TO_VIDEO nhưng chưa bật backend video AI thật -> "
                                f"{new_method} ({reality})]")
                elif plan_source == "ai":
                    new_method = route["production_method"]
                    note = f" [AI trả phương pháp không hợp lệ '{method}' -> router]"
                elif is_scifi_topic:
                    keep_archive = method == "REAL_ARCHIVE" and reality in (REAL_FILMABLE, DEEP_SEA_MYSTERY)
                    new_method = method if keep_archive else route["production_method"]
                    if not keep_archive:
                        t["tool"] = route["tool"]
                        t["rationale"] = (d_meta or {}).get("rationale") or route["rationale"]
                else:
                    new_method = method

                if new_method != method:
                    t["production_method"] = new_method
                    if "strategy" in t or t is strat_by_id.get(sid):
                        t["strategy"] = self._map_method_to_legacy_strategy(new_method)
                if note:
                    t["rationale"] = f"{t.get('rationale') or ''}{note}"

                # Từ khóa: giữ từ khóa của AI nếu cụ thể; nếu chung chung ("cinematic") dùng truy vấn theo đúng cảnh
                q = str(t.get("search_query") or "").strip()
                if q.lower() in self._GENERIC_QUERIES or len(q.split()) < 2:
                    q = route["search_query"] or q
                queries = [q] + [x for x in route["search_queries"] if x != q]
                t["search_query"] = q
                t["search_queries"] = queries[:6]
                t["reality_class"] = reality
                t["prefer_generation"] = reality == PURE_FICTION and new_method in ("IMAGE_TO_VIDEO", "TEXT_TO_VIDEO")

                if d_meta and d_meta.get("is_scifi"):
                    if plan_source != "ai" or not t.get("ai_prompt"):
                        t["ai_prompt"] = d_meta["ai_prompt"]
                    t["camera_motion"] = d_meta.get("camera_motion", t.get("camera_motion", "zoom_in"))
                    t["sfx_cue"] = d_meta.get("sfx_cue", t.get("sfx_cue", "whoosh_fast"))
                    t["is_scifi"] = True
                    t["reject_static_slideshow"] = True

                if t is strat_by_id.get(sid):
                    if plan_source != "ai":
                        stats["router"] += 1 if is_scifi_topic else 0
                    elif note:
                        stats["capability_fix"] += 1
                    else:
                        stats["kept_ai"] += 1

        logger.info(
            f"🧭 [VideoStrategist] Định tuyến công cụ (nguồn kế hoạch: {plan_source}, video AI thật: {true_t2v}): "
            f"giữ quyết định AI {stats['kept_ai']}, hiệu chỉnh năng lực {stats['capability_fix']}, router quyết {stats['router']}"
        )

    def _propose_with_ai(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Optional[Dict[str, Any]]:
        """Sử dụng Gemini phân tích chuyên sâu từng cảnh theo 4 phương pháp cốt lõi."""
        scenes_context = []
        for s in scenes:
            scenes_context.append({
                "scene_id": s.get("scene_id"),
                "narration": s.get("narration"),
                "visual_keyword": s.get("visual_keyword"),
                "visual_prompt": s.get("visual_prompt"),
                "media_type": s.get("media_type", "auto"),
            })

        from app.agents.visual_tool_router import get_generation_capabilities
        caps = get_generation_capabilities()
        if caps.get("true_text_to_video"):
            capability_note = ("- CÓ backend video AI thật (TEXT_TO_VIDEO tạo chuyển động thật).\n"
                               "- Kho video: Pexels, Pixabay, NASA. Ảnh AI: Gemini/Pollinations.")
        else:
            capability_note = ("- CHƯA CÓ backend video AI thật: TEXT_TO_VIDEO và IMAGE_TO_VIDEO đều chỉ là 1 ẢNH AI TĨNH + hiệu ứng "
                               "zoom/pan. Đừng chọn TEXT_TO_VIDEO với kỳ vọng có chuyển động thật.\n"
                               "- Kho video (Pexels, Pixabay, NASA) cho chuyển động THẬT 30-60fps -> ưu tiên cho REAL_FILMABLE và "
                               "SCIENTIFIC_VISUALIZATION. Dùng ảnh AI (IMAGE_TO_VIDEO) cho PURE_FICTION và ẩn dụ mà kho không thể có.\n"
                               "- Ảnh AI miễn phí hay vẽ SAI loài vật / địa danh có thật -> tránh dùng AI cho động vật, địa danh thật.")

        system_prompt = f"""
Bạn là TỔNG ĐẠO DIỄN THỊ GIÁC & GIÁM ĐỐC SẢN XUẤT (Executive Visual Director & Lead Showrunner) của chuỗi kênh YouTube Shorts triệu view hàng đầu thế giới (tiêu chuẩn điện ảnh A24, điều tra phóng sự Vox, kỳ vĩ National Geographic/Melodysheep, trực quan Kurzgesagt).

══════════════════════════════════════════════════════════════════════════════
NHIỆM VỤ TỐI THƯỢNG: CỰC ĐẠI HÓA RETENTION (TỶ LỆ GIỮ CHÂN KHÁN GIẢ > 100%)
══════════════════════════════════════════════════════════════════════════════
Khán giả YouTube Shorts vuốt qua màn hình cực kỳ tàn nhẫn (Swipe-away < 1.8s) nếu không có xung kích thị giác mạnh. 
Đạo diễn xuất sắc KHÔNG BAO GIỜ phó mặc cho sự ngẫu nhiên, mà là CHỈ HUY VÀ GIAO NHIỆM VỤ CHÍNH XÁC CHO CÁC BỘ PHẬN CẤP DƯỚI (Workers / Sub-Agents) dựa trên kịch bản từng giây.

══════════════════════════════════════════════════════════════════════════════
I. CẤU TRÚC GIỮ CHÂN 4 NHỊP (THE 4-BEAT RETENTION ARCHITECTURE)
══════════════════════════════════════════════════════════════════════════════
Mọi video Shorts phải tuân thủ nghiêm ngặt 4 giai đoạn nhịp điệu thị giác:

1. BEAT 1: HOOK ĐỘT PHÁ (0 - 3s) — "Scroll-Stopper":
   - Cảnh 1 PHẢI là cú sốc thị giác mạnh nhất (Cận cảnh góc hiểm sắc lạnh, chuyển động camera gia tốc hyper_zoom, hoặc tư liệu gây tò mò tột độ). 
   - Tuyệt đối cấm cảnh mở màn tẻ nhạt, chậm chạp hoặc dùng ảnh tĩnh đứng yên.

2. BEAT 2: THIẾT LẬP BỐI CẢNH & ĐƯA RA BẰNG CHỨNG (3 - 15s) — "The Setup & Evidence":
   - Đưa khán giả vào sâu trong câu chuyện bằng hình ảnh có tính xác thực cao (tư liệu lịch sử thật, bản đồ, video b-roll chuyển động thực tế 60fps). 
   - Tạo dựng sự tin cậy và tò mò không thể dứt ra.

3. BEAT 3: LEO THANG XUNG ĐỘT & CAO TRÀO (15 - 45s) — "The Escalation & Pattern Interrupt":
   - Nhịp điệu dồn dập: Cứ mỗi 3 giây phải có một điểm ngắt thị giác (Visual Pattern Interrupt) — đan xen nhịp nhàng giữa bằng chứng tư liệu và đại cảnh kỳ vĩ, hoặc giữa cận cảnh biểu cảm sắc bén và chuyển động toàn cảnh.

4. BEAT 4: CÚ CHỐT & VÒNG LẶP LIÊN TỤC (45 - 60s) — "The Climax & Infinite Loop":
   - Kết thúc bằng một khung hình ám ảnh hoặc mở ra một câu hỏi ngỏ dẫn dắt người xem quay ngược trở lại câu Hook ở đầu video, kích thích người xem xem lại lần 2 (Looping Retention).

══════════════════════════════════════════════════════════════════════════════
II. BẢN ĐỒ BẢN CHẤT CẢNH (REALITY ONTOLOGY)
══════════════════════════════════════════════════════════════════════════════
Trước khi chọn công cụ, Đạo diễn phải phân loại đúng bản chất của cảnh:

- 'REAL_FILMABLE': Thứ có thật ngoài đời và đã được quay (phi hành gia, tên lửa, thành phố đêm, xe cộ, động vật săn mồi, thí nghiệm, con người thực tế) -> Footage thật luôn thuyết phục hơn AI.
- 'DEEP_SEA_MYSTERY' / 'NATURAL_MYSTERY': Bí ẩn đại dương, quái vật biển sâu, thủy quái truyền thuyết, thực thể kinh dị (Kraken, Megalodon, Leviathan, Cthulhu, rãnh Mariana, quái vật hồ Loch Ness, The Bloop, Titanoboa, SCP, Wendigo...) -> KHÁN GIẢ MUỐN CẢM GIÁC RÙNG RỢN, ÁM ẢNH TỘT ĐỘ (Thalassophobia / Megalophobia / Dark Horror)! PHẢI ƯU TIÊN SĂN ẢNH HOT-TREND RÙNG RỢN NHẤT TRÊN GOOGLE IMAGES (Cảnh phim CGI điện ảnh, ArtStation 8K Dark Horror, bóng đen khổng lồ dưới đáy biển sâu áp đảo tàu ngầm) KẾT HỢP CHUYỂN ĐỘNG 3D ('IMAGE_TO_VIDEO') và B-roll vực thẳm đại dương đen ngòm ('STOCK_FOOTAGE'). TUYỆT ĐỐI CẤM tìm ảnh hóa thạch răng bảo tàng, xác cá/mực chết dạt bờ hay tranh khắc gỗ thế kỷ 18 khô khan!
- 'SCIENTIFIC_VISUALIZATION': Hiện tượng khoa học vĩ mô/vi mô có thật nhưng không thể quay bằng máy ảnh thường (hố đen, sao neutron, siêu tân tinh, Big Bang, cấu trúc nguyên tử) -> Kho stock có sẵn mô phỏng CGI/NASA chất lượng cao.
- 'PURE_FICTION': Hoàn toàn không tồn tại trong lịch sử lẫn nghiên cứu tự nhiên (thành phố năm 3000, quả cầu Dyson, cỗ máy thời gian, hạm đội người ngoài hành tinh) -> Mới sinh bằng AI hoặc tìm Concept Art 8K.
- 'ABSTRACT_CONCEPT': Khái niệm trừu tượng (ý thức, thuyết mô phỏng, nghịch lý thời gian, ẩn dụ tâm lý học).

══════════════════════════════════════════════════════════════════════════════
III. ĐẠO DIỄN CHỈ THỊ & PHÂN CÔNG NHIỆM VỤ CHO CẤP DƯỚI (DELEGATION PROTOCOL)
══════════════════════════════════════════════════════════════════════════════
Đạo diễn có trong tay 3 bộ phận công nhân chuyên trách (Subordinate Workers):

1. BỘ PHẬN SĂN LÙNG TƯ LIỆU & ẢNH HOT-TREND MẠNG MỞ ('REAL_ARCHIVE' - Google Images / Open Web / Wikipedia):
   - KHI NÀO GIAO VIỆC: 
     * Nhân vật lịch sử có thật, sự kiện lịch sử, di tích cổ đại, bằng chứng khoa học thực nghiệm.
   - LỆNH CHỈ ĐẠO CHO CẤP DƯỚI ('search_query'): Viết câu lệnh tiếng Anh 3-6 từ sắc bén để tìm bức ảnh đắt giá, ấn tượng nhất trên Google Images.
   - Hiệu ứng: Hệ thống tự động áp dụng hiệu ứng Ken Burns 3D Deep Zoom / Pan biến ảnh thành thước phim sống động!

2. BỘ PHẬN QUAY PHIM B-ROLL THỰC TẾ ('STOCK_FOOTAGE' - Pexels / Pixabay 60fps):
   - KHI NÀO GIAO VIỆC: Cảnh có hành động vật lý thực tế, biểu cảm con người đời thường, thiên nhiên dữ dội (sóng biển cuộn, bão tố, sấm sét), ĐẠI DƯƠNG ĐEN NGÒM RÙNG RỢN (vực thẳm đáy biển tối tăm, đèn pha tàu ngầm rọi bóng tối, thợ lặn trong hang ngầm tối).
   - LỆNH CHỈ ĐẠO CHO CẤP DƯỚI ('search_query'): Viết từ khóa b-roll khí quyển u tối, kịch tính (Ví dụ: 'dark deep ocean abyssal underwater', 'submarine spotlight pitch black water', 'dark storm ocean waves night').

3. BỘ PHẬN SĂN ẢNH QUÁI VẬT RÙNG RỢN & HỌA SĨ ĐIỆN ẢNH 3D ('IMAGE_TO_VIDEO' / 'TEXT_TO_VIDEO'):
   - KHI NÀO GIAO VIỆC: 
     * QUÁI VẬT, THỦY QUÁI, SINH VẬT TIỀN SỬ & KINH DỊ (Kraken, Megalodon, Leviathan, Loch Ness, The Bloop, Titanoboa, Cthulhu, SCP, Wendigo...): BẮT BUỘC dùng 'IMAGE_TO_VIDEO' với 'search_query' săn ảnh Movie CGI / ArtStation Dark Horror / Thalassophobia rùng rợn (Ví dụ: 'terrifying giant kraken tentacles attacking ship cgi', 'terrifying megalodon shark jaws dark ocean movie scene', 'colossal sea monster thalassophobia megalophobia artstation') kết hợp 'ai_prompt' cực kỳ rùng rợn, quy mô khổng lồ áp đảo (Megalophobia), ánh sáng u tối lạnh gáy!
     * Chủ đề CẦN ẨN DỤ NGHỆ THUẬT & TƯ DUY ĐIỆN ẢNH hoặc cảnh viễn tưởng PURE_FICTION.
   - LỆNH CHỈ ĐẠO CHO CẤP DƯỚI ('ai_prompt'): Viết câu lệnh tiếng Anh điện ảnh 8K 9:16 chi tiết, làm rõ quy mô khổng lồ rùng rợn (terrifying colossal scale, thalassophobia, megalophobia), ánh sáng tương phản mạnh (submersible spotlight piercing pitch-black abyss, glowing eyes in darkness, chiaroscuro), góc máy (extreme low-angle, POV).

══════════════════════════════════════════════════════════════════════════════
IV. CHỈ ĐẠO MÁY QUAY & ÂM THANH (CINEMATOGRAPHY & SOUND DESIGN)
══════════════════════════════════════════════════════════════════════════════
- 'camera_motion':
  * 'hyper_zoom': Cú lao máy chớp nhoáng (bắt buộc cho Hook Scene 1 hoặc cú twist).
  * 'zoom_in': Tiến chậm vào chủ thể, tạo áp lực tâm lý và kịch tính.
  * 'zoom_out': Lùi dần để lộ ra đại cảnh kinh ngạc hoặc quy mô khổng lồ.
  * 'cinematic_drift': Trôi dạt chậm rãi, tạo không khí bí ẩn, tĩnh lặng trước cơn bão.
  * 'pan_left' / 'pan_right': Quét ngang khám phá bằng chứng hoặc chi tiết toàn cảnh.

- 'sfx_cue':
  * 'impact_sub': Cú nện bass rung chuyển (cho Hook mở đầu hoặc cao trào va chạm).
  * 'sub_drop': Tiếng sóng âm sonar rúng động đáy sâu đại dương.
  * 'whoosh_fast': Tiếng vút gió chuyển cảnh tốc độ cao (Visual Pattern Interrupt).
  * 'tension_riser': Tiếng rít tăng tiến căng thẳng, đẩy nhịp tim người xem.
  * 'heartbeat': Nhịp tim dồn dập trong khoảnh khắc nghẹt thở.

══════════════════════════════════════════════════════════════════════════════
V. QUY TẮC ĐAN XEN NHỊP ĐIỆU (RHYTHMIC WEAVING RULES)
══════════════════════════════════════════════════════════════════════════════
- TUYỆT ĐỐI KHÔNG để 2 cảnh cùng kiểu ảnh tĩnh nối tiếp nhau!
- Với chủ đề Động vật / Thiên nhiên / Đời thực: 70% - 90% là STOCK_FOOTAGE.
- Với chủ đề Bí ẩn đại dương / Quái vật biển / Kinh dị: 60% IMAGE_TO_VIDEO (Săn ảnh Movie CGI / ArtStation Horror / Thalassophobia rùng rợn + chuyển động 3D Ken Burns) đan xen 40% STOCK_FOOTAGE (B-roll vực thẳm đại dương đen ngòm / hành lang tối) để vừa có chuyển động thật vừa nổi da gà ở mọi khung hình!
- Với chủ đề Tâm lý học / Khắc kỷ / Triết học: 50% - 70% IMAGE_TO_VIDEO đan xen STOCK_FOOTAGE hành động đời thực.

══════════════════════════════════════════════════════════════════════════════
NĂNG LỰC THẬT CỦA MÁY CHỦ (BẮT BUỘC TÔN TRỌNG):
══════════════════════════════════════════════════════════════════════════════
{capability_note}

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Thể loại kênh: {channel_niche}
- Các phân cảnh kịch bản:
{json.dumps(scenes_context, ensure_ascii=False, indent=2)}

HÃY XUẤT RA JSON DUY NHẤT THEO SCHEMA:
{{
  "topic_classification": "HISTORICAL_DOCUMENTARY | NATURAL_MYSTERY | PSYCHOLOGY_STOICISM | SCIENTIFIC_DEEPDIVE | SCI_FI_FANTASY",
  "director_overview": "Tầm nhìn nghệ thuật tổng thể (Màu sắc, phong cách, nhịp điệu đan xen 4 nhịp)",
  "retention_strategy": "Chiến lược cụ thể để giữ chân người xem trong 3s đầu và tạo vòng lặp loop ở cuối video",
  "scene_proposals": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Tư duy logic đạo diễn: Phân tích mâu thuẫn kịch bản và lý giải cách chuyển hóa thành hình ảnh/hành động kịch tính cụ thể",
      "retention_beat": "HOOK_0_3S | SETUP_3_15S | ESCALATION_15_45S | CLIMAX_LOOP_45_60S",
      "reality_class": "REAL_FILMABLE | DEEP_SEA_MYSTERY | SCIENTIFIC_VISUALIZATION | PURE_FICTION | ABSTRACT_CONCEPT",
      "production_method": "REAL_ARCHIVE | STOCK_FOOTAGE | IMAGE_TO_VIDEO | TEXT_TO_VIDEO",
      "tool": "wikimedia_archive | pexels_video_api | imagen3_i2v | wan_t2v",
      "rationale": "Giải thích chi tiết 1 câu vì sao chọn phương pháp & công cụ này cho cảnh này (bằng tiếng Việt)",
      "search_query": "2-4 từ tiếng Anh CỤ THỂ CHỈ ĐỊNH CẤP DƯỚI TÌM KIẾM (ví dụ: 'colossal giant squid specimen', '18th century kraken engraving', 'deep ocean submarine lights'; cấm từ chung chung như 'cinematic')",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K 9:16 do Đạo diễn tự sáng tạo logic theo kịch bản (dành cho AI hoặc dự phòng nếu kho stock thiếu)",
      "camera_motion": "hyper_zoom | zoom_in | zoom_out | cinematic_drift | pan_left | pan_right",
      "sfx_cue": "impact_sub | sub_drop | whoosh_fast | tension_riser | heartbeat"
    }}
  ]
}}"""
        raw = self._call_gemini(system_prompt)
        if not raw:
            return None

        try:
            plan = json.loads(raw)
            if "scene_proposals" in plan and isinstance(plan["scene_proposals"], list):
                for p in plan["scene_proposals"]:
                    sq = p.get("search_query") or "cinematic"
                    ap = p.get("ai_prompt") or f"Cinematic 8k dramatic vertical film still of {sq}, rich atmospheric lighting, vivid contrast, 35mm photography, 9:16"
                    p["search_query"] = sq
                    p["ai_prompt"] = ap

                plan["scenes_strategy"] = [
                    {
                        "scene_id": p.get("scene_id"),
                        "reality_class": p.get("reality_class"),
                        "strategy": self._map_method_to_legacy_strategy(p.get("production_method")),
                        "production_method": p.get("production_method"),
                        "tool": p.get("tool"),
                        "rationale": p.get("rationale"),
                        "search_query": p.get("search_query"),
                        "ai_prompt": p.get("ai_prompt"),
                        "camera_motion": p.get("camera_motion", "zoom_in"),
                        "sfx_cue": p.get("sfx_cue", "whoosh"),
                        "retention_beat": p.get("retention_beat", "ESCALATION_15_45S"),
                    }
                    for p in plan["scene_proposals"]
                ]
                return plan
        except Exception as e:
            logger.warning(f"[VideoStrategist] Lỗi parse JSON từ Gemini: {e}")

        return None

    def _propose_with_heuristics(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Dict[str, Any]:
        """Tự động phân tích và ra quyết định bằng bộ quy tắc nghiệp vụ chặt chẽ."""
        topic_lower = topic.lower()
        is_mystery_or_fantasy = any(w in topic_lower for w in [
            "mystery", "mysteries", "secret", "alien", "ufo", "space", "galaxy", "supernatural",
            "paranormal", "magic", "fantasy", "myth", "monster", "ghost", "dimension", "portal",
            "time travel", "future", "huyền bí", "bí ẩn", "vũ trụ", "tâm linh", "giả tưởng", "kỳ lạ", "lỗ đen",
            "viễn tưởng", "khoa học viễn tưởng", "sci-fi", "scifi", "science fiction", "cyberpunk",
            "futuristic", "robot", "người máy", "trí tuệ nhân tạo", "ai", "không gian", "ngân hà", "hố đen",
            "ngoài hành tinh", "phi thuyền", "hành tinh", "chiến tranh giữa các vì sao", "vũ trụ quan"
        ])

        is_historical = (bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")) or any(w in topic_lower for w in [
            "history", "war", "battle", "emperor", "king", "ancient", "century", "empire",
            "lịch sử", "thế chiến", "hoàng đế", "vua", "cổ đại", "triều đại", "di tích"
        ])

        is_psychology_or_philosophy = any(w in topic_lower or channel_niche.lower() for w in [
            "psychology", "stoic", "stoicism", "mindset", "tâm lý", "khắc kỷ", "triết lý", "thao túng",
            "manipulation", "dark psychology", "tư duy", "triết học", "nghĩ", "bản ngã", "ego", "focus",
            "human behavior", "psychological", "habits", "mental", "thói quen"
        ])

        topic_class = "HISTORICAL_DOCUMENTARY" if is_historical else (
            "MYSTERY_ABSTRACT" if is_mystery_or_fantasy else (
                "CINEMATIC_STORY" if is_psychology_or_philosophy else "REAL_LIFE_MOTION"
            )
        )

        motion_cues = {
            "walk", "run", "running", "moving", "movement", "crowd", "people", "street",
            "traffic", "rain", "storm", "fire", "water", "sea",
            "ocean", "wave", "waves", "underwater", "deep sea", "fish", "whale", "submarine", "radar",
            "dive", "diving", "swimming", "boat", "ship", "depth", "abyss", "clock", "time", "hourglass",
            "typing", "money", "cash",
            "door", "window", "sky", "clouds", "flight", "car", "nature",
            # Sci-Fi / Không gian / Viễn tưởng B-Roll
            "spaceship", "space", "galaxy", "stars", "planet", "alien planet", "wormhole", "black hole",
            "cyberpunk", "neon", "futuristic", "robot", "android", "laser", "portal", "orbit", "cosmos",
            "technology", "hologram", "warp", "speed", "flight", "flying", "universe", "nebula",
            "phi thuyền", "ngân hà", "hành tinh", "vũ trụ", "lỗ đen", "công nghệ", "viễn tưởng"
        }

        abstract_fantasy_cues = {
            "energy", "glowing", "glow", "cosmic", "universe", "alien", "portal", "dimensional",
            "supernatural", "secret", "magic", "spirit", "floating", "beam", "ray", "power",
            "mystic", "huyền bí", "phép thuật", "năng lượng", "lỗ đen", "vũ trụ", "vô tận"
        }

        psychology_ai_cues = {
            "portrait", "face", "philosopher", "chân dung", "khuôn mặt", "statue", "monk", "warrior",
            "creature", "monster", "leviathan", "eye", "eyes", "stare", "gaze", "whisper", "shadow",
            "silhouette", "alone", "mind", "brain", "thought", "mask", "manipulation", "reflection",
            "mirror", "stoic", "dark", "ego", "fear", "tension", "interrogation", "unblinking",
            "gaslighting", "soul", "subconscious", "thao túng", "tâm trí", "ảo giác"
        }

        motions_list = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
        scene_proposals = []
        scenes_strategy = []

        for idx, sc in enumerate(scenes):
            sc_id = sc.get("scene_id", idx + 1)
            narr = (sc.get("narration") or "").lower()
            kw = (sc.get("visual_keyword") or "").lower()
            vp = sc.get("visual_prompt") or ""
            sq = sc.get("stock_query") or kw
            text_all = f"{narr} {kw} {sq}".lower()

            user_pref = sc.get("media_type")

            # ── 1. Xét chỉ định thủ công từ người dùng hoặc kịch bản (nếu có) ──
            if user_pref == "ai_image":
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Chỉ định kịch bản / Người dùng: Tạo ảnh nghệ thuật 8K siêu nét rồi chuyển hóa thành video 3D."
            elif user_pref == "ai_video":
                method = "TEXT_TO_VIDEO"
                tool = "wan_t2v"
                rationale = "Chỉ định thủ công: Sinh Video AI trực tiếp cho cảnh này."
            elif user_pref == "video":
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Chỉ định thủ công: Dùng Video B-Roll chuyển động 9:16."
            elif user_pref in ("photo", "documentary"):
                if primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure"):
                    method = "REAL_ARCHIVE"
                    tool = "wikimedia_archive"
                    rationale = "Chỉ định thủ công: Tìm ảnh tư liệu lịch sử thực tế."
                else:
                    method = "STOCK_FOOTAGE"
                    tool = "pexels_photo_api"
                    rationale = "Chỉ định thủ công: Tìm ảnh chụp chất lượng cao (Pexels Photo)."

            # ── 2. Đạo Diễn Tự Quyết Định Tối Ưu (Auto Strategist & Dynamic Weaving) ──
            # Quy tắc 1: Thực thể lịch sử thật (từ Cảnh 2 trở đi hoặc khi nhắc đích danh) -> REAL_ARCHIVE
            elif primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure") and (idx == 1 or primary_entity.lower() in text_all):
                method = "REAL_ARCHIVE"
                tool = "wikimedia_archive"
                rationale = f"Nhắc đến nhân vật/địa danh lịch sử có thật '{primary_entity}' -> dùng ảnh tư liệu thật 100% để đảm bảo tính xác thực."

            # Quy tắc 2: Hiện tượng huyền bí, vũ trụ siêu thực không có hình thái cố định -> TEXT_TO_VIDEO
            elif any(fc in text_all for fc in abstract_fantasy_cues) or (is_mystery_or_fantasy and idx in (2, 3)):
                method = "TEXT_TO_VIDEO"
                tool = "wan_t2v"
                rationale = "Ý tưởng huyền bí / năng lượng trừu tượng không có hình thể cố định -> sinh Video AI trực tiếp để tạo sự choáng ngợp."

            # Quy tắc 3: Chủ đề Tâm lý học / Khắc kỷ / Bí ẩn / Triết học -> BẮT BUỘC ƯU TIÊN IMAGE_TO_VIDEO (8K AI + 3D Camera)
            elif is_psychology_or_philosophy and (idx == 0 or any(pc in text_all for pc in psychology_ai_cues)):
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Chủ đề Tâm lý / Khắc kỷ: Tạo ảnh nghệ thuật 8K điện ảnh giàu chiều sâu, biểu cảm sắc nét, chuyển hóa thành video 3D Parallax mượt mà."

            # Cảnh 1 Hook cho các chủ đề khác: Video B-Roll chuyển động mạnh để giữ chân khán giả
            elif idx == 0:
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Cảnh 1 (Primal Hook): Bắt buộc dùng Video B-Roll chuyển động 9:16 để giật sự chú ý, chống lướt qua."

            # Quy tắc 4: Cảnh có nhân vật, chân dung cụ thể hoặc sinh vật đặc thù cần độ nét tuyệt đối -> IMAGE_TO_VIDEO
            elif any(c in text_all for c in ["portrait", "face", "philosopher", "chân dung", "khuôn mặt", "statue", "monk", "warrior", "creature", "monster", "leviathan"]):
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Cảnh cần đặc tả nhân vật/sinh vật cụ thể -> tạo ảnh chủ đề 8K chuẩn bố cục rồi chuyển hóa thành video 3D."

            # Quy tắc 5: Chuyển động đời thực, bối cảnh viễn tưởng, không gian, b-roll động -> STOCK_FOOTAGE
            elif any(mc in text_all for mc in motion_cues):
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Cảnh miêu tả chuyển động hoặc bối cảnh đời thực -> dùng Video B-Roll Pexels 9:16 giữ chân người xem bằng video động."

            # Quy tắc mặc định: Đan xen nhịp nhàng giữa Video Động và Ảnh AI Chuyển động 3D
            else:
                prev_method = scene_proposals[-1]["production_method"] if scene_proposals else ""
                if is_psychology_or_philosophy:
                    # Tâm lý học: 70% IMAGE_TO_VIDEO, 30% STOCK_FOOTAGE
                    method = "STOCK_FOOTAGE" if (prev_method == "IMAGE_TO_VIDEO" and idx % 3 == 0) else "IMAGE_TO_VIDEO"
                    tool = "pexels_video_api" if method == "STOCK_FOOTAGE" else "imagen3_i2v"
                    rationale = "Đan xen nhịp nhàng: Giữ tính nghệ thuật 8K AI độc bản kết hợp B-roll thực tế."
                elif prev_method in ("IMAGE_TO_VIDEO", "REAL_ARCHIVE"):
                    # Nếu cảnh trước đã là ảnh tĩnh/I2V -> Cảnh này chuyển sang Video động
                    method = "STOCK_FOOTAGE"
                    tool = "pexels_video_api"
                    rationale = "Đan xen nhịp điệu: Sau cảnh tĩnh, chuyển sang Video B-roll động 9:16 để video thở và giữ nhịp."
                else:
                    method = "IMAGE_TO_VIDEO" if (idx % 3 == 1) else "STOCK_FOOTAGE"
                    tool = "imagen3_i2v" if method == "IMAGE_TO_VIDEO" else "pexels_video_api"
                    rationale = "Đảm bảo video chuyển động sống động (Full Video 9:16) kết hợp chuyển động camera mượt mà."

            cam_motion = motions_list[idx % len(motions_list)]
            sfx = "whoosh" if idx == 0 else "hit"
            legacy_strat = self._map_method_to_legacy_strategy(method)

            item = {
                "scene_id": sc_id,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": sq or kw or "cinematic",
                "ai_prompt": vp or f"Cinematic 8k vertical shot of {kw or 'scene'}, highly detailed, dramatic lighting, 9:16",
                "camera_motion": cam_motion,
                "sfx_cue": sfx
            }
            scene_proposals.append(item)

            scenes_strategy.append({
                "scene_id": sc_id,
                "strategy": legacy_strat,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": item["search_query"],
                "ai_prompt": item["ai_prompt"],
                "camera_motion": cam_motion,
                "sfx_cue": sfx
            })

        return {
            "topic_classification": topic_class,
            "director_overview": f"Chiến lược sản xuất đa phương pháp ({topic_class}): Cân bằng hoàn hảo giữa tư liệu thật, video B-roll, video từ ảnh chủ đề (I2V) và video AI trực tiếp (T2V).",
            "scene_proposals": scene_proposals,
            "scenes_strategy": scenes_strategy,
        }

    @staticmethod
    def _map_method_to_legacy_strategy(method: str) -> str:
        """Map từ 4 production methods sang các strategy tương thích."""
        mapping = {
            "REAL_ARCHIVE": "REAL_DOCUMENTARY",
            "IMAGE_TO_VIDEO": "AI_VIDEO",
            "TEXT_TO_VIDEO": "AI_VIDEO",
            "STOCK_FOOTAGE": "STOCK_VIDEO",
        }
        return mapping.get(method, "STOCK_VIDEO")
