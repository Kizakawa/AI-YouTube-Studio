"""
app.agents.video_strategist_agent — Tác nhân Đề Xuất Chiến Lược & Phương Pháp Sản Xuất Video (Video Production Strategist Agent).

Nhiệm vụ cốt lõi:
Phân tích toàn diện kịch bản tổng thể (`full_script_story`), ngữ cảnh ngầm (subtext), chủ đề, nhân vật và thể loại kênh
bằng tư duy ngữ nghĩa của LLM (Zero Keyword-Patching) để lập Bản Đề Xuất Kỹ Thuật Làm Video (Video Production Plan).
"""
import json
import logging
from typing import Dict, Any, List, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)


class VideoStrategistAgent:
    """
    Tác nhân Đề Xuất Chiến Lược & Phương Pháp Làm Video (Video Production Strategist).
    Đóng vai trò Tổng Đạo Diễn Hình Ảnh (Director of Photography) trong tiền kỳ sản xuất.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini(self, prompt: str, timeout: int = 20) -> Optional[str]:
        """Gọi Gemini REST API với KeyRotator ưu tiên Model mạnh nhất trước."""
        if not self.rotator:
            return None

        import requests
        for model, key in self.rotator.iter_best_model_key_pairs(AVAILABLE_MODELS):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
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
                elif res.status_code in (429, 403, 404, 503):
                    self.rotator.mark_model_rate_limited(key, model, status_code=res.status_code, error_text=res.text[:250])
                    continue
            except Exception as e:
                logger.debug(f"[VideoStrategist] Gemini call error on {model}: {e}")
                continue

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
        Lập bản đề xuất chiến lược kỹ thuật sản xuất video cho từng cảnh dựa trên tư duy ngữ nghĩa toàn kịch bản của AI Strategist.
        """
        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        scifi_agent = SciFiVisualDispatcherAgent()
        is_scifi_topic, conf, subgenre, _ = scifi_agent.detect_scifi_elements(
            topic=topic,
            channel_niche=channel_niche,
        )

        # 1. Gọi AI Strategist (bộ não chính hiểu sâu toàn bộ kịch bản)
        ai_proposal = self._propose_with_ai(topic, scenes, primary_entity, entity_type, channel_niche)
        if ai_proposal and "scene_proposals" in ai_proposal and len(ai_proposal["scene_proposals"]) == len(scenes):
            logger.info("🎬 [VideoStrategist] ✅ Đã phê duyệt Bản Đề Xuất Chiến Lược Sản Xuất Video từ AI Strategist!")
            final_plan = ai_proposal
            plan_source = "ai"
        else:
            # 2. Fallback nhẹ theo dữ liệu cảnh khi toàn bộ Gemini API tạm offline
            logger.info("🎬 [VideoStrategist] Kích hoạt Context-Preserving Strategist Fallback...")
            final_plan = self._propose_with_heuristics(topic, scenes, primary_entity, entity_type, channel_niche)
            plan_source = "heuristic"

        if final_plan:
            final_plan["plan_source"] = plan_source
            self._apply_tool_routing(
                final_plan, scenes, topic, channel_niche, plan_source,
                is_scifi_topic, subgenre, scifi_agent,
            )
            if is_scifi_topic:
                final_plan["is_scifi_video"] = True
                final_plan["scifi_subgenre"] = subgenre

        if final_plan and not is_scifi_topic:
            try:
                from app.services.media.query_broadener import is_real_world_nature_topic
                is_nature_fallback = (plan_source != "ai") and is_real_world_nature_topic(topic)
                if is_nature_fallback or plan_source == "ai":
                    switched = 0
                    scene_by_id = {s.get("scene_id"): s for s in scenes}
                    for key in ("scenes_strategy", "scene_proposals"):
                        for strat in final_plan.get(key) or []:
                            should_switch = (
                                (plan_source == "ai" and strat.get("reality_class") == "REAL_FILMABLE" and channel_niche in ("wildlife_predators", "animal_rescue", "nature"))
                                or (is_nature_fallback and strat.get("production_method") in ("IMAGE_TO_VIDEO", "TEXT_TO_VIDEO"))
                            )
                            if should_switch and strat.get("production_method") in ("IMAGE_TO_VIDEO", "TEXT_TO_VIDEO"):
                                sc = scene_by_id.get(strat.get("scene_id"), {})
                                strat["production_method"] = "STOCK_FOOTAGE"
                                strat["strategy"] = "STOCK_VIDEO"
                                strat["tool"] = "pexels_video_api"
                                if sc.get("stock_query") and strat.get("search_query") in (None, "", "cinematic"):
                                    strat["search_query"] = sc["stock_query"]
                                strat["rationale"] = "Chủ đề thiên nhiên đời thực (REAL_FILMABLE): ưu tiên footage thật, AI chỉ là dự phòng."
                                if key == "scenes_strategy":
                                    switched += 1
                    if switched:
                        final_plan["real_footage_priority"] = True
                        logger.info(f"🌿 [VideoStrategist] REAL_FILMABLE -> chuyển {switched} cảnh sang footage thật.")
            except Exception as e:
                logger.debug(f"[VideoStrategist] Nature real-footage enforcement error: {e}")

        return final_plan

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
            VisualToolRouter, PURE_FICTION, REAL_FILMABLE, DEEP_SEA_MYSTERY, DOODLE_PSYCHOLOGY_EXPLAINER, REALITY_KEYWORDS,
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
            route = router.route_scene(
                sc,
                topic=topic,
                subgenre_queries=(d_meta or {}).get("search_queries"),
                channel_niche=channel_niche,
            )

            ai_reality = str(targets[0].get("reality_class") or "").upper()
            reality = ai_reality if ai_reality in REALITY_KEYWORDS else route["reality_class"]
            if route["reality_class"] == DOODLE_PSYCHOLOGY_EXPLAINER:
                reality = DOODLE_PSYCHOLOGY_EXPLAINER

            for t in targets:
                method = t.get("production_method")
                note = ""
                if reality == DOODLE_PSYCHOLOGY_EXPLAINER:
                    new_method = "IMAGE_TO_VIDEO"
                    t["tool"] = "doodle_2d_animator"
                elif plan_source == "ai" and method in valid_methods:
                    new_method = method
                    if method == "TEXT_TO_VIDEO" and not true_t2v:
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
        """Sử dụng Gemini đọc hiểu toàn bộ kịch bản và phân tích chuyên sâu từng cảnh."""
        full_script_story = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
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
Bạn là TỔNG ĐẠO DIỄN THỊ GIÁC & GIÁM ĐỐC SẢN XUẤT (Executive Visual Director & Lead Showrunner) của chuỗi kênh YouTube Shorts triệu view hàng đầu thế giới.

══════════════════════════════════════════════════════════════════════════════
0. QUY TRÌNH TƯ DUY NGỮ NGHĨA 2 BƯỚC (HOLISTIC SCRIPT & SUBTEXT REASONING — CHỐNG PHỤ THUỘC TỪ KHÓA):
══════════════════════════════════════════════════════════════════════════════
- BƯỚC 1 (Đọc hiểu toàn bộ kịch bản, Giải mã đại từ & Ẩn dụ):
  * Hãy đọc kỹ TOÀN BỘ CỐT TRUYỆN (`FULL SCRIPT STORY`) trước khi phân tích từng câu nhỏ.
  * Tự động giải mã mọi đại từ ("Nó", "Họ", "Anh ta", "It", "He", "They", "This creature", "Ele", "Ela") và khôi phục chủ thể bị ẩn về đúng nhân vật/thực thể chính của kịch bản (`{primary_entity or topic}`).
  * Giải mã các phép ẩn dụ, so sánh hoặc con số thống kê sang HÀNH ĐỘNG VẬT LÝ CỤ THỂ của chủ thể chính (Ví dụ: lời thoại nói "hàm răng ép như máy thủy lực 1.500 PSI" hoặc "chiếc lồng vô hình của sự im lặng" -> tuyệt đối KHÔNG tìm máy thủy lực hay cái lồng sắt, mà phải tìm cảnh chủ thể chính đang săn mồi hoặc biểu cảm căng thẳng giữa hai người!).
- BƯỚC 2 (Chỉ đạo công cụ & truy vấn bám sát mạch truyện):
  * Mọi `search_query` và `ai_prompt` của từng cảnh phải tự đứng vững khi tách rời: luôn chứa tên chủ thể chính + hành động/bối cảnh thực sự trong câu chuyện, không bao giờ lặp lại cùng một truy vấn giữa 2 cảnh liền kề.

══════════════════════════════════════════════════════════════════════════════
I. CẤU TRÚC GIỮ CHÂN 4 NHỊP (THE 4-BEAT RETENTION ARCHITECTURE)
══════════════════════════════════════════════════════════════════════════════
1. BEAT 1: HOOK ĐỘT PHÁ (0 - 3s) — "Scroll-Stopper":
   - Cảnh 1 PHẢI là cú sốc thị giác mạnh nhất (chuyển động camera gia tốc hyper_zoom, hành động trực diện hoặc tư liệu gây tò mò tột độ).
2. BEAT 2: THIẾT LẬP BỐI CẢNH & BẰNG CHỨNG (3 - 15s) — "The Setup & Evidence":
   - Đưa khán giả vào sâu trong câu chuyện bằng hình ảnh có tính xác thực cao.
3. BEAT 3: LEO THANG XUNG ĐỘT & CAO TRÀO (15 - 45s) — "The Escalation & Pattern Interrupt":
   - Đan xen nhịp nhàng giữa bằng chứng tư liệu và đại cảnh kỳ vĩ, hoặc giữa cận cảnh biểu cảm sắc bén và chuyển động toàn cảnh.
4. BEAT 4: CÚ CHỐT & VÒNG LẶP LIÊN TỤC (45 - 60s) — "The Climax & Infinite Loop":
   - Kết thúc bằng khung hình ấn tượng nối mượt mà về câu Hook ở đầu video.

══════════════════════════════════════════════════════════════════════════════
II. BẢN ĐỒ BẢN CHẤT CẢNH (REALITY ONTOLOGY)
══════════════════════════════════════════════════════════════════════════════
- 'REAL_FILMABLE': Thứ có thật ngoài đời và đã được quay (động vật hoang dã, thiên nhiên, phi hành gia, tên lửa, thành phố, con người thực tế) -> Ưu tiên Footage thật ('STOCK_FOOTAGE') kết hợp ảnh Top Search Google Images sắc nét.
- 'DEEP_SEA_MYSTERY' / 'NATURAL_MYSTERY': Bí ẩn đại dương, quái vật biển sâu, thủy quái truyền thuyết, thực thể kinh dị (Kraken, Megalodon, Leviathan, Cthulhu, rãnh Mariana, The Bloop, Titanoboa, SCP...) -> Ưu tiên 'IMAGE_TO_VIDEO' với `search_query` 2–5 từ tiếng Anh săn ảnh CGI / VFX / Thalassophobia rùng rợn áp đảo quy mô đan xen 'STOCK_FOOTAGE' vực thẳm đại dương.
- 'SCIENTIFIC_VISUALIZATION': Hiện tượng khoa học vĩ mô/vi mô (hố đen, sao neutron, siêu tân tinh, cấu trúc nguyên tử) -> Kho stock mô phỏng CGI/NASA chất lượng cao.
- 'PURE_FICTION': Hoàn toàn không tồn tại (thành phố năm 3000, quả cầu Dyson, cỗ máy thời gian) -> Sinh bằng AI hoặc tìm Concept Art 8K.
- 'ABSTRACT_CONCEPT': Khái niệm trừu tượng, tâm lý học, thao túng tâm lý, triết học -> Dịch sang hành vi/biểu cảm con người cụ thể (Dark Noir / Chiaroscuro).
- 'DOODLE_PSYCHOLOGY_EXPLAINER': Các chủ đề Tâm lý học Đen (Dark Psychology / Gaslighting), Nghịch lý cuộc sống hiện đại (Modern Rat-Race), Tài chính cá nhân & Bẫy tiền bạc (Personal Finance), Cơ chế não bộ & Dopamine Detox, hoặc khi kênh thuộc ngách `satirical_psychology_animation` / prompt có thẻ `@MODERNYOU`, `@PLEASER`, `@MANIPULATOR`, `@ANCESTOR`, `@FARMER` -> BẮT BUỘC chọn `production_method: "IMAGE_TO_VIDEO"` và `tool: "doodle_2d_animator"` (Dựng hoạt họa 2D Doodle 24fps nét vẽ tay trên nền giấy kem kèm đạo cụ ẩn dụ trực quan).

══════════════════════════════════════════════════════════════════════════════
NĂNG LỰC THẬT CỦA MÁY CHỦ (BẮT BUỘC TÔN TRỌNG):
══════════════════════════════════════════════════════════════════════════════
{capability_note}

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Thể loại kênh: {channel_niche}
- TOÀN BỘ CỐT TRUYỆN (FULL SCRIPT STORY):
"{full_script_story}"
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
      "visual_reasoning": "Giải mã đại từ/ẩn dụ từ kịch bản tổng thể và lý giải cách chuyển hóa thành hình ảnh cụ thể",
      "retention_beat": "HOOK_0_3S | SETUP_3_15S | ESCALATION_15_45S | CLIMAX_LOOP_45_60S",
      "reality_class": "REAL_FILMABLE | DEEP_SEA_MYSTERY | SCIENTIFIC_VISUALIZATION | PURE_FICTION | ABSTRACT_CONCEPT | DOODLE_PSYCHOLOGY_EXPLAINER",
      "production_method": "REAL_ARCHIVE | STOCK_FOOTAGE | IMAGE_TO_VIDEO | TEXT_TO_VIDEO",
      "tool": "wikimedia_archive | pexels_video_api | imagen3_i2v | wan_t2v | doodle_2d_animator",
      "rationale": "Giải thích chi tiết 1 câu vì sao chọn phương pháp & công cụ này cho cảnh này (bằng tiếng Việt)",
      "search_query": "2-5 từ tiếng Anh NGẮN GỌN, CỤ THỂ (Chủ thể đã giải mã đại từ + hành động/bối cảnh thật của cảnh này; cấm trùng lặp giữa các cảnh)",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K 9:16 do Đạo diễn tự sáng tạo theo ngữ cảnh kịch bản",
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
                seen_sqs: set = set()
                scene_by_id = {s.get("scene_id"): s for s in scenes}
                for p in plan["scene_proposals"]:
                    sc_ref = scene_by_id.get(p.get("scene_id"), {})
                    sq = str(p.get("search_query") or sc_ref.get("visual_keyword") or "cinematic").strip()
                    if sq.lower() in seen_sqs and sc_ref.get("visual_keyword"):
                        vk_alt = str(sc_ref.get("visual_keyword")).strip()
                        if vk_alt.lower() not in seen_sqs:
                            sq = vk_alt
                    seen_sqs.add(sq.lower())
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
        """
        Dự phòng giữ nguyên ngữ cảnh của từng phân cảnh (Context-Preserving Fallback) khi Gemini tạm thời offline.
        Không dùng danh sách từ khóa cứng để đoán mò mà bám sát `media_type`, `entity_type`, `channel_niche` và truy vấn gốc của cảnh.
        """
        niche_low = (channel_niche or "").lower()
        is_historical = bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")
        is_psych = "psych" in niche_low or "stoic" in niche_low
        topic_class = "HISTORICAL_DOCUMENTARY" if is_historical else ("PSYCHOLOGY_STOICISM" if is_psych else "REAL_LIFE_MOTION")

        motions_list = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
        scene_proposals = []
        scenes_strategy = []
        subj_anchor = (primary_entity or topic or "").strip()

        for idx, sc in enumerate(scenes):
            sc_id = sc.get("scene_id", idx + 1)
            kw = (sc.get("visual_keyword") or "").strip()
            vp = (sc.get("visual_prompt") or "").strip()
            sq = (sc.get("stock_query") or kw or subj_anchor or "cinematic").strip()
            user_pref = sc.get("media_type")

            if user_pref == "ai_image":
                method, tool = "IMAGE_TO_VIDEO", "imagen3_i2v"
                rationale = "Chỉ định từ kịch bản: Tạo khung hình 8K kết hợp chuyển động camera 3D."
            elif user_pref == "ai_video":
                method, tool = "TEXT_TO_VIDEO", "wan_t2v"
                rationale = "Chỉ định từ kịch bản: Sinh Video AI chuyển động trực tiếp."
            elif user_pref == "video":
                method, tool = "STOCK_FOOTAGE", "pexels_video_api"
                rationale = "Chỉ định từ kịch bản: Dùng Video B-Roll chuyển động thực tế 9:16."
            elif user_pref in ("photo", "documentary") and is_historical:
                method, tool = "REAL_ARCHIVE", "wikimedia_archive"
                rationale = f"Tư liệu xác thực cho thực thể '{primary_entity}'."
            elif is_historical and idx in (1, 2):
                method, tool = "REAL_ARCHIVE", "wikimedia_archive"
                rationale = f"Tư liệu lưu trữ thực tế cho '{primary_entity}'."
            elif idx == 0:
                method, tool = "STOCK_FOOTAGE", "pexels_video_api"
                rationale = "Cảnh 1 Hook: Ưu tiên Video chuyển động mạnh giữ chân khán giả."
            else:
                prev_method = scene_proposals[-1]["production_method"] if scene_proposals else ""
                if is_psych and prev_method != "IMAGE_TO_VIDEO":
                    method, tool = "IMAGE_TO_VIDEO", "imagen3_i2v"
                    rationale = "Đan xen khung hình biểu cảm chiều sâu tâm lý và B-roll."
                elif prev_method in ("IMAGE_TO_VIDEO", "REAL_ARCHIVE"):
                    method, tool = "STOCK_FOOTAGE", "pexels_video_api"
                    rationale = "Đan xen nhịp điệu: Chuyển sang Video B-roll động 9:16 để giữ nhịp."
                else:
                    method = "IMAGE_TO_VIDEO" if (idx % 3 == 1) else "STOCK_FOOTAGE"
                    tool = "imagen3_i2v" if method == "IMAGE_TO_VIDEO" else "pexels_video_api"
                    rationale = "Đan xen cân bằng giữa chuyển động thực tế và khung hình đặc tả."

            cam_motion = "hyper_zoom" if idx == 0 else motions_list[idx % len(motions_list)]
            sfx = "impact_sub" if idx == 0 else "whoosh_fast"
            legacy_strat = self._map_method_to_legacy_strategy(method)
            ai_p = vp or f"Cinematic 8k vertical shot of {kw or sq}, dramatic lighting, 9:16"

            item = {
                "scene_id": sc_id,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": sq,
                "ai_prompt": ai_p,
                "camera_motion": cam_motion,
                "sfx_cue": sfx,
            }
            scene_proposals.append(item)
            scenes_strategy.append({
                "scene_id": sc_id,
                "strategy": legacy_strat,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": sq,
                "ai_prompt": ai_p,
                "camera_motion": cam_motion,
                "sfx_cue": sfx,
            })

        return {
            "topic_classification": topic_class,
            "director_overview": f"Chiến lược sản xuất đa phương pháp ({topic_class}) bám sát ngữ cảnh kịch bản.",
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
