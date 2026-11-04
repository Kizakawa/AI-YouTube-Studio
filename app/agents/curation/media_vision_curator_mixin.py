"""
app.agents.curation.media_vision_curator_mixin — Thẩm định & tuyển chọn Video/Ảnh ứng viên bằng Multimodal Gemini Vision theo ngữ cảnh kịch bản.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MediaVisionCuratorMixin:
    """Mixin đánh giá và chấm điểm ứng viên Video/Ảnh bằng Gemini Vision dựa trên sự thấu hiểu kịch bản."""

    @staticmethod
    def _fetch_video_thumbnails_for_llm(
        eval_pool: List[Dict[str, Any]],
        timeout: float = 4.5,
    ) -> Tuple[List[Tuple[str, str]], Dict[int, int]]:
        """
        Tải song song ảnh preview (thumbnail) của ứng viên video Pexels/Pixabay
        và đóng dấu INDEX để Gemini Vision nhìn trực tiếp nội dung bên trong video.
        """
        import base64 as _b64
        import io as _io
        import requests as _req
        from concurrent.futures import ThreadPoolExecutor
        from PIL import Image, ImageDraw, ImageFilter, ImageStat

        def _dl_one(item_pair: Tuple[int, Dict[str, Any]]) -> Optional[Tuple[int, str, str]]:
            idx, cand = item_pair
            t_url = str(cand.get("thumbnail") or "").strip()
            if not t_url or not t_url.startswith("http"):
                return None
            try:
                r = _req.get(
                    t_url,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                if r.status_code != 200 or not r.content or len(r.content) < 800:
                    return None
                with Image.open(_io.BytesIO(r.content)) as im:
                    if im.mode != "RGB":
                        im = im.convert("RGB")
                    # Lọc bỏ các thumbnail bị hỏng/đen kịt/cháy trắng hoàn toàn trước khi gửi LLM
                    # (lưu ý: ảnh preview nén web của cảnh dưới nước/đại dương thường có biên cạnh mềm ~2.5-4.5, không lọc quá gắt như frame gốc 1080p)
                    g256 = im.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                    g_st = ImageStat.Stat(g256)
                    lum_m = float(g_st.mean[0])
                    con_s = float(g_st.stddev[0])
                    sh_m = float(ImageStat.Stat(g256.filter(ImageFilter.FIND_EDGES)).mean[0])
                    cand["_thumb_sharpness"] = round(sh_m, 2)
                    cand["_thumb_contrast"] = round(con_s, 2)
                    if lum_m < 12.0 or lum_m > 246.0 or con_s < 8.5 or sh_m < 2.2:
                        cand["_thumb_low_quality"] = True
                        return None
                    im.thumbnail((320, 320), Image.Resampling.BILINEAR)
                    try:
                        draw = ImageDraw.Draw(im)
                        w_im, _ = im.size
                        slug_short = str(cand.get("slug") or "")[:28]
                        label_txt = f"INDEX {idx}: {slug_short}"
                        draw.rectangle([(0, 0), (w_im, 22)], fill=(0, 0, 0))
                        draw.text((6, 4), label_txt, fill=(255, 255, 0))
                    except Exception:
                        pass
                    buf = _io.BytesIO()
                    im.save(buf, format="JPEG", quality=74)
                    b64_str = _b64.b64encode(buf.getvalue()).decode("utf-8")
                    return idx, "image/jpeg", b64_str
            except Exception:
                return None

        images_out: List[Tuple[str, str]] = []
        idx_to_order: Dict[int, int] = {}
        if not eval_pool:
            return images_out, idx_to_order

        from concurrent.futures import as_completed
        ex = ThreadPoolExecutor(max_workers=min(8, len(eval_pool)))
        results: List[Optional[Tuple[int, str, str]]] = []
        try:
            futs = [ex.submit(_dl_one, item) for item in enumerate(eval_pool)]
            for fut in as_completed(futs, timeout=max(2.5, timeout + 1.2)):
                try:
                    r_val = fut.result()
                    if r_val is not None:
                        results.append(r_val)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

        results.sort(key=lambda x: x[0] if x else 999)
        for res in results:
            if res is None:
                continue
            c_idx, mime, b64_data = res
            idx_to_order[c_idx] = c_idx
            images_out.append((mime, b64_data))

        return images_out, idx_to_order

    def curate_candidate_videos(
        self,
        candidates: List[Dict[str, Any]],
        visual_analysis: Dict[str, Any],
        narration: str = "",
        used_urls: Optional[set] = None,
        topic: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """
        Đánh giá toàn diện danh sách video ứng viên trả về từ API Pexels/Pixabay bằng LLM + Multimodal Vision
        dựa trên ý nghĩa thực sự của phân cảnh trong kịch bản người dùng.
        """
        from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx
        if is_scene_cancelled_ctx(min_remaining_sec=1.5):
            return None, 0.0, "Scene deadline reached before video curation.", False
        used = used_urls or set()
        if not candidates:
            return None, 0.0, "Không có ứng viên video nào từ kho tư liệu.", True

        def _res_pref(vf: Dict[str, Any], v_dur: float = 0.0) -> int:
            h = vf.get("height", 0) or 0
            fps = float(vf.get("fps") or 30.0)
            fps_penalty = 2500 if (v_dur > 20.0 and fps > 35.0) else (800 if fps > 50.0 else 0)
            if v_dur > 40.0 and 1280 <= h < 1920:
                return 10500 - fps_penalty
            if h == 1920:
                return 10000 - fps_penalty
            if 1280 <= h < 1920:
                return 8500 + h - fps_penalty
            if 720 <= h < 1280:
                return 6000 + h - fps_penalty
            if h > 1920:
                return 4000 - h // 10 - fps_penalty
            return h - fps_penalty

        is_long_fmt = str((visual_analysis or {}).get("video_format") or "short").lower() == "long"

        def _res_pref_landscape(vf: Dict[str, Any], v_dur: float = 0.0) -> int:
            w = int(vf.get("width", 0) or 0)
            h = int(vf.get("height", 0) or 0)
            fps = float(vf.get("fps") or 30.0)
            fps_penalty = 2000 if (v_dur > 25.0 and fps > 35.0) else (600 if fps > 50.0 else 0)
            if w == 1920 and h == 1080:
                return 11000 - fps_penalty
            if 1060 <= h <= 1100 and w >= 1900:
                return 10000 - fps_penalty
            if 720 <= h < 1080 and w > h:
                return 8500 + h - fps_penalty
            if h > 1080 and w > h:
                return 4500 - h // 10 - fps_penalty
            return h - fps_penalty

        from app.services.media.media_history import is_media_already_used
        valid_candidates = []
        seen_creators_in_pool: set = set()
        for v in candidates:
            url = v.get("url", "")
            if not url or is_media_already_used(url, used):
                continue
            raw_user = v.get("user") if isinstance(v.get("user"), dict) else {}
            creator_key = str(
                v.get("creator_key")
                or (f"pexels_user:{raw_user.get('id')}" if raw_user.get("id") else "")
                or (f"pixabay_user:{v.get('user_id')}" if v.get("user_id") else "")
            ).strip()
            if creator_key and (creator_key in used or creator_key in seen_creators_in_pool):
                continue

            v_files = v.get("video_files", [])
            if not v_files:
                if v.get("media_type", "video") != "video":
                    continue
                vw = int(v.get("width", 1920) or 1920)
                vh = int(v.get("height", 1080) or 1080)
                if is_long_fmt and vw <= vh:
                    continue
                if creator_key:
                    seen_creators_in_pool.add(creator_key)
                valid_candidates.append({
                    "raw_video": v,
                    "url": url,
                    "download_link": url,
                    "width": vw,
                    "height": vh,
                    "duration": v.get("duration", 0),
                    "slug": re.sub(r"^(Pixabay Video|NASA[^:]*):\s*", "", v.get("title", "")) or v.get("tags") or "video clip",
                    "thumbnail": v.get("thumbnail", ""),
                    "source": v.get("source", "stock_video"),
                    "creator_key": creator_key,
                })
                continue

            if is_long_fmt:
                matched_files = [
                    vf for vf in v_files
                    if int(vf.get("width", 0) or 0) > int(vf.get("height", 0) or 0)
                    and int(vf.get("height", 0) or 0) >= 720
                    and (str(vf.get("link", "")).endswith(".mp4") or "mp4" in str(vf.get("file_type", "")))
                ]
            else:
                matched_files = [
                    vf for vf in v_files
                    if vf.get("width", 0) <= vf.get("height", 0) and (
                        vf.get("link", "").endswith(".mp4") or "mp4" in vf.get("file_type", "")
                    )
                ]
            if not matched_files:
                continue

            slug = ""
            m = re.search(r"/video/([a-zA-Z0-9-]+)-\d+/?", url)
            if m:
                slug = m.group(1).replace("-", " ")
            else:
                slug = v.get("tags") or v.get("title") or "video clip"

            v_dur_val = float(v.get("duration") or 0.0)
            pref_fn = _res_pref_landscape if is_long_fmt else _res_pref
            best_file = sorted(matched_files, key=lambda vf: pref_fn(vf, v_dur_val), reverse=True)[0]
            dl_link = best_file.get("link")
            if not dl_link or is_media_already_used(str(dl_link), used):
                continue
            if creator_key:
                seen_creators_in_pool.add(creator_key)
            valid_candidates.append({
                "raw_video": v,
                "url": url,
                "download_link": dl_link,
                "width": best_file.get("width", 1920 if is_long_fmt else 1080),
                "height": best_file.get("height", 1080 if is_long_fmt else 1920),
                "duration": v.get("duration", 0),
                "slug": slug,
                "thumbnail": v.get("image", ""),
                "source": "pexels_16x9_video" if is_long_fmt else "pexels_video",
                "creator_key": creator_key,
            })

        if not valid_candidates:
            return None, 0.0, "Tất cả ứng viên đều không đạt định dạng hoặc đã được sử dụng.", True

        if not is_long_fmt:
            portrait_only = [
                c for c in valid_candidates
                if (int(c.get("height") or 0) + 10) >= int(c.get("width") or 0)
            ]
            if portrait_only:
                valid_candidates = portrait_only
            valid_candidates.sort(key=lambda c: 0 if (c["height"] or 0) >= (c["width"] or 0) else 1)
        else:
            valid_candidates.sort(key=lambda c: 0 if (c["width"] or 0) > (c["height"] or 0) else 1)
        eval_pool = valid_candidates[:8]

        def _format_winner(
            winner: Dict[str, Any],
            score: float,
            rationale: str,
            alternate_urls: Optional[List[str]] = None,
            vision_curated: bool = True,
        ) -> Dict[str, Any]:
            out_w = {
                "url": winner["download_link"],
                "web_url": winner["url"],
                "title": f"{winner['source']}: {winner['slug']}",
                "width": winner["width"],
                "height": winner["height"],
                "duration": winner["duration"],
                "media_type": "video",
                "source": winner["source"],
                "thumbnail": winner["thumbnail"],
                "curation_score": score,
                "curation_rationale": rationale,
                "vision_curated": vision_curated,
            }
            if winner.get("creator_key"):
                out_w["creator_key"] = winner["creator_key"]
            if alternate_urls:
                out_w["_vision_alternates"] = alternate_urls
            return out_w

        thumb_images, idx_to_img_order = self._fetch_video_thumbnails_for_llm(eval_pool)

        mood = visual_analysis.get("mood", "cinematic")
        metaphor = visual_analysis.get("visual_metaphor", "")
        subtext = visual_analysis.get("narrative_subtext", "")
        subject_req = visual_analysis.get("subject") or visual_analysis.get("core_visual_entity") or ""
        topic_ctx = topic or visual_analysis.get("topic") or subject_req
        expected_vis = visual_analysis.get("expected_visual_content") or metaphor
        forbidden_fp = visual_analysis.get("forbidden_false_positives") or ""
        negative_cues = list(visual_analysis.get("negative_cues", []))
        if forbidden_fp:
            negative_cues.append(str(forbidden_fp))

        candidates_for_llm = []
        for idx, c in enumerate(eval_pool):
            if c.get("_thumb_low_quality"):
                continue
            img_ord = idx_to_img_order.get(idx)
            candidates_for_llm.append({
                "index": idx,
                "returned_url": c["url"],
                "slug_or_tags": c["slug"],
                "source": c["source"],
                "duration": f"{c['duration']}s",
                "resolution": f"{c['width']}x{c['height']}",
                "attached_preview_image": (
                    f"INDEX {img_ord} (stamped 'INDEX {img_ord}' at top of preview image)"
                    if img_ord is not None
                    else "None (no visual preview available — cannot visually verify specific entity morphology)"
                ),
            })

        if not candidates_for_llm:
            return None, 2.5, "Tất cả ứng viên video kho đều quá mờ hoặc thiếu tương phản (< chuẩn VisualCritic).", True

        prompt = f"""Bạn là Tổng Đạo Diễn Hình Ảnh & Giám Khảo Thẩm Định Ngữ Cảnh Kịch Bản Đa Phương Thức (Multimodal Script-Intent Video Auditor).
Hãy đọc hiểu sâu Chủ đề kịch bản của người dùng, Lời thoại phân cảnh, Hàm ý câu chuyện và NHÌN TRỰC TIẾP vào các ảnh preview (`INDEX 0`, `INDEX 1`...) kèm đường link trả về để tự chấm điểm nghiêm ngặt và chọn ra VIDEO truyền tải SÁT Ý NHẤT tinh thần kịch bản.

NGỮ CẢNH KỊCH BẢN CỦA NGƯỜI DÙNG:
- Chủ đề tổng thể (Topic): "{topic_ctx}"
- Chủ thể cốt lõi của cảnh (Resolved Subject): "{subject_req}"
- Lời thoại phân cảnh (Narration): "{narration}"
- Hàm ý câu chuyện (Narrative Subtext): "{subtext}"
- Hình ảnh kỳ vọng: "{expected_vis}" (Tâm trạng: "{mood}")
- Các yếu tố sai lệch cần loại bỏ: {json.dumps(negative_cues, ensure_ascii=False)}

DANH SÁCH ỨNG VIÊN VIDEO TỪ KHO:
{json.dumps(candidates_for_llm, ensure_ascii=False, indent=2)}

QUY TRÌNH SUY LUẬN & TỰ CHẤM ĐIỂM ĐA TRỤC (MULTI-AXIS SELF-SCORING RUBRIC):
1. QUY CHIẾU ĐẠI TỪ & DANH TỪ CHUNG VỀ THỰC THỂ ĐÍCH DANH (`resolved_scene_entities`):
   - Khi `{narration}` sử dụng đại từ hoặc danh từ chung/ẩn dụ (ví dụ: "the hunter", "its prey", "this creature", "the leader", "the machine"), BẮT BUỘC phải quy chiếu ngược về đúng Thực thể Đích danh trong `{topic_ctx}` và `{subject_req}`.
   - Tuyệt đối KHÔNG chọn một thực thể/chủng loại khác chỉ vì khớp từ khóa danh từ chung trong câu thoại hay URL slug.
2. TỰ CHẤM ĐIỂM 3 TRỤC ĐỘC LẬP CHO ỨNG VIÊN TỐT NHẤT (Thang điểm 0.0–10.0):
   - `entity_identity_score` (0.0–10.0): Nếu ứng viên có xuất hiện chủ thể, hình thái nhận diện của chủ thể trong ảnh preview có khớp chính xác với Thực thể Đích danh của `{topic_ctx}` / `{subject_req}` hay không?
     + Nếu ứng viên thể hiện sai thực thể đích danh (dù cùng nhóm chung), hoặc là chủ thể đặc thù nhưng không có ảnh preview (`attached_preview_image == None`) để xác minh hình thái, BẮT BUỘC chấm `entity_identity_score < 5.0`.
     + Trường hợp câu thoại mô tả pha tương tác đa thực thể hiếm, ứng viên quay thật và đúng chính xác MỘT trong các thực thể đích danh của `{topic_ctx}` hoặc quay bối cảnh môi trường tự nhiên/lịch sử đúng câu thoại (không chứa chủ thể sai lệch) được chấm `7.8–8.5`.
   - `narrative_action_score` (0.0–10.0): Hành động, trạng thái vật lý và bối cảnh trong ứng viên có truyền tải đúng nội dung của chunk `{narration}` hay không?
   - `visual_integrity_score` (0.0–10.0): Khung hình có nguyên bản, tự nhiên đúng bối cảnh kịch bản (không lẫn yếu tố nhân tạo/nuôi nhốt/hiện đại lệch ngữ cảnh, không bị đơn sắc/đen trắng lệch tông, không chứa chữ/watermark/biểu tượng AI `✦`) hay không?
3. QUYẾT ĐỊNH DUYỆT HOẶC TỰ ĐỘNG CHUYỂN TẦNG (`confidence_score = min(entity_identity_score, narrative_action_score, visual_integrity_score)`):
   - Chỉ duyệt ứng viên khi CẢ 3 TRỤC đều đạt `>= 7.5` (`confidence_score >= 7.5`, `should_fallback_to_ai: false`). Đồng thời chỉ liệt kê vào `approved_alternate_indices` các ứng viên khác cũng đạt cả 3 trục `>= 7.5`.
   - Nếu ứng viên cao nhất có bất kỳ trục nào `< 7.5`, BẮT BUỘC trả về `"winner_index": -1`, `"should_fallback_to_ai": true` để bộ điều phối chuyển sang luồng tìm kiếm chuyên sâu.

TRẢ VỀ JSON DUY NHẤT:
{{
  "resolved_scene_entities": "Tên thực thể đích danh được quy chiếu từ Topic cho câu thoại này",
  "winner_index": -1,
  "winner_url": "",
  "entity_identity_score": 2.0,
  "narrative_action_score": 2.0,
  "visual_integrity_score": 2.0,
  "confidence_score": 2.0,
  "approved_alternate_indices": [],
  "rationale": "Giải thích điểm 3 trục của ứng viên và lý do duyệt hoặc từ chối",
  "should_fallback_to_ai": true
}}"""
        agent_dec = self._call_gemini_json(prompt, images=thumb_images if thumb_images else None)
        if agent_dec is not None:
            w_idx = int(agent_dec.get("winner_index", -1))
            w_url = str(agent_dec.get("winner_url") or "").strip()
            raw_conf = float(agent_dec.get("confidence_score", 0.0))
            ent_sc = float(agent_dec.get("entity_identity_score", raw_conf))
            act_sc = float(agent_dec.get("narrative_action_score", raw_conf))
            int_sc = float(agent_dec.get("visual_integrity_score", raw_conf))
            score = round(min(raw_conf, ent_sc, act_sc, int_sc), 2)
            rationale = str(agent_dec.get("rationale", ""))
            fallback_ai = bool(agent_dec.get("should_fallback_to_ai", False)) or (score < 7.5)

            if w_url and not fallback_ai:
                url_matched_idx = next((i for i, c in enumerate(eval_pool) if c["url"] == w_url), None)
                if url_matched_idx is not None and url_matched_idx != w_idx:
                    w_idx = url_matched_idx

            if 0 <= w_idx < len(eval_pool) and score >= 7.5 and not fallback_ai:
                winner = eval_pool[w_idx]
                alt_indices: List[int] = []
                for a_i in (agent_dec.get("approved_alternate_indices") or []):
                    try:
                        ai_int = int(str(a_i).strip())
                        if 0 <= ai_int < len(eval_pool) and ai_int != w_idx and ai_int not in alt_indices:
                            alt_indices.append(ai_int)
                    except Exception:
                        pass
                # Chỉ lấy các ứng viên dự phòng đã được Gemini Vision phê duyệt trong `approved_alternate_indices`
                # (tuyệt đối không tự ghép mù các ứng viên khác trong eval_pool theo từ khóa vì sẽ lọt các clip đã bị Vision loại bỏ).
                alt_urls = [
                    eval_pool[ai_int]["download_link"]
                    for ai_int in alt_indices
                    if eval_pool[ai_int].get("download_link") and eval_pool[ai_int]["download_link"] != winner.get("download_link")
                ]
                logger.info(
                    f"🎬 [LLM Link & Vision Audit] ✅ Đã chọn Video #{winner['raw_video'].get('id')} "
                    f"(INDEX {w_idx}: '{winner['url']}', backups={len(alt_urls)}) | Điểm: {score}/10 | {rationale}"
                )
                return _format_winner(winner, score, rationale, alternate_urls=alt_urls), score, rationale, False

            logger.info(
                f"🚫 [LLM Link & Vision Audit] Từ chối toàn bộ ứng viên video lệch kịch bản "
                f"(Score: {score}/10: {rationale})"
            )
            return None, score, rationale, True

        # Cứu hộ xác định (Deterministic Subject-Anchor Fallback) KHI VÀ CHỈ KHI Gemini Vision API bị 429/503 toàn cục:
        # Không chọn mù ngẫu nhiên! Chỉ duyệt ứng viên Pexels/Pixabay khi:
        # (1) Vượt qua bộ lọc EntityDetector chống lạc đề (`is_incongruent_stock_image == False`)
        # (2) Slug/URL của clip chứa từ khóa thực thể chủ đạo (`subject_req` / `topic_ctx` / `expected_vis`)
        try:
            from app.services.media.entity_detector import is_incongruent_stock_image
            import re as _re_fb
            _stop_fb = {
                "the", "and", "for", "with", "from", "into", "over", "under", "this", "that",
                "video", "footage", "shot", "view", "close", "slow", "motion", "cinematic", "dark",
                "foot", "feet", "inch", "pound", "ton", "tons", "mile", "miles", "year", "years",
                "day", "days", "hour", "hours", "second", "seconds", "minute", "minutes", "half",
                "part", "rate", "mode", "design", "crew", "people", "person", "world", "life",
                "massive", "extreme", "giant", "entire", "afternoon", "overnight", "against",
            }
            scene_queries_str = " ".join(
                str(x) for x in ((visual_analysis or {}).get("search_queries") or []) if x
            )
            neg_tokens = {
                w for w in _re_fb.findall(r"[a-zA-Z]{4,}", " ".join(str(x) for x in negative_cues).lower())
                if w not in _stop_fb
            }
            _habitat_generic_fb = {
                "desert", "savanna", "african", "africa", "jungle", "forest", "ocean", "water",
                "river", "mountain", "nature", "animal", "animals", "wildlife", "wild", "night",
                "glow", "fire", "flame", "flames", "sand", "rock", "ground", "walking", "standing",
                "running", "hunting", "attack", "aerial", "valley", "island", "arctic", "tundra",
                "snow", "winter", "tropical", "deep", "underwater", "predator", "prey", "species",
                "dog", "dogs", "cat", "cats", "bird", "birds", "fish", "man", "woman", "human",
                "pet", "pets", "breed", "rescue", "cinematic", "motion", "closeup", "dramatic",
                "intense", "atmospheric", "sunny", "relaxing", "outdoors",
            }
            eff_subj_str = f"{subject_req} {expected_vis} {scene_queries_str} {topic_ctx}".strip()
            core_subj_tokens = {
                w for w in _re_fb.findall(r"[a-zA-Z]{4,}", eff_subj_str.lower())
                if w not in _stop_fb and w not in _habitat_generic_fb
            }
            scene_intent_tokens = {
                w for w in _re_fb.findall(r"[a-zA-Z]{3,}", f"{scene_queries_str} {expected_vis}".lower())
                if w not in _stop_fb
            }
            narr_tokens = {
                w for w in _re_fb.findall(r"[a-zA-Z]{3,}", f"{narration}".lower())
                if w not in _stop_fb
            }
            min_req_anchors = 2 if len(core_subj_tokens | scene_intent_tokens) >= 2 else 1
            best_det_cand = None
            best_det_score = 0.0
            for cand in eval_pool:
                slug_txt = f"{cand.get('slug', '')} {cand.get('url', '')}".lower()
                if is_incongruent_stock_image(slug_txt, topic=topic_ctx, narration=narration):
                    continue
                cand_tokens = set(_re_fb.findall(r"[a-zA-Z]{3,}", slug_txt))
                if neg_tokens and cand_tokens.intersection(neg_tokens - core_subj_tokens - scene_intent_tokens):
                    continue
                subj_hits = len(cand_tokens.intersection(core_subj_tokens))
                intent_hits = len(cand_tokens.intersection(scene_intent_tokens))
                narr_hits = len(cand_tokens.intersection(narr_tokens))
                distinct_anchor_hits = len(cand_tokens.intersection(core_subj_tokens | scene_intent_tokens))
                # Bắt buộc khớp ít nhất 1 từ khóa chủ thể đặc trưng (subj_hits >= 1) hoặc >= 2 từ khóa ngữ cảnh phân cảnh (intent_hits >= 2)
                if (subj_hits >= 1 and distinct_anchor_hits >= 1) or (intent_hits >= 2 and distinct_anchor_hits >= min_req_anchors):
                    is_vert = (cand.get("height") or 0) >= (cand.get("width") or 0)
                    det_sc = min(8.2, 7.2 + subj_hits * 0.35 + intent_hits * 0.25 + min(narr_hits, 2) * 0.1 + (0.2 if is_vert else 0.0))
                    if det_sc > best_det_score:
                        best_det_score = det_sc
                        best_det_cand = cand
            if best_det_cand is not None and best_det_score >= 7.2:
                rationale_fb = f"Deterministic Subject-Anchor Match ({best_det_score:.1f}/10 while Gemini Vision API rate-limited)"
                logger.info(
                    f"🎬 [LLM Link & Vision Audit] ⚡ Cứu hộ Subject-Anchor chọn Video #{best_det_cand['raw_video'].get('id')} "
                    f"('{best_det_cand['url']}') | Điểm: {best_det_score:.1f}/10"
                )
                return _format_winner(best_det_cand, round(best_det_score, 2), rationale_fb, vision_curated=False), round(best_det_score, 2), rationale_fb, False
        except Exception as _e_det_fb:
            logger.debug(f"[LLM Link & Vision Audit] Deterministic fallback skip: {_e_det_fb}")

        logger.warning("⚠️ [LLM Link & Vision Audit] Không thể kết nối Gemini Vision -> Từ chối chọn mù!")
        return None, 0.0, "Gemini Vision unavailable — rejected unverified stock videos", True

    def check_media_congruence(
        self,
        media_desc: str,
        topic: str = "",
        narration: str = "",
        visual_keyword: str = "",
    ) -> Tuple[bool, float, str]:
        """
        Thẩm định ngữ nghĩa xem mô tả ảnh/video có ăn khớp với tinh thần kịch bản của người dùng hay bị lạc đề/lệch thời đại.
        """
        if not media_desc:
            return False, 5.0, "Không có mô tả tư liệu"

        prompt = f"""Bạn là Giám Khảo Thẩm Định Ngữ Cảnh Kịch Bản (Script Context & Congruence Critic) cho YouTube Shorts.
Hãy đánh giá xem tư liệu bên dưới có phù hợp với câu chuyện và phân cảnh trong kịch bản của người dùng hay bị LẠC ĐỀ HOÀN TOÀN (`is_incongruent: true`).

NGỮ CẢNH KỊCH BẢN CỦA NGƯỜI DÙNG:
- Chủ đề câu chuyện (Topic): "{topic}"
- Lời thoại phân cảnh (Narration): "{narration}"
- Ý đồ hình ảnh (Visual Intent): "{visual_keyword}"

TƯ LIỆU CẦN THẨM ĐỊNH:
- Mô tả / Tiêu đề / Thẻ: "{media_desc}"

NGUYÊN TẮC SUY LUẬN NGỮ NGHĨA:
- Đừng chỉ so khớp từ khóa bề mặt! Hãy hiểu câu chuyện của người dùng đang nói về chủ thể nào, ở thời đại/môi trường nào.
- Đánh dấu `is_incongruent: true` (`congruence_score < 5.0`) nếu tư liệu rơi vào:
  1. Lệch chủ thể & thể loại (ví dụ: văn phòng hiện đại/tiệc tùng lọt vào kịch bản cổ đại, vũ trụ hoặc thiên nhiên hoang dã; hoặc động vật lọt vào kịch bản tâm lý con người).
  2. Lệch thời đại hoặc môi trường sinh thái so với kịch bản.
  3. Bản đồ hành chính, biểu đồ, sách báo có chữ khi kịch bản cần cảnh điện ảnh.

TRẢ VỀ JSON DUY NHẤT:
{{
  "is_incongruent": false,
  "congruence_score": 8.0,
  "rationale": "Giải thích 1 câu ngắn gọn dựa trên sự thấu hiểu kịch bản"
}}"""
        res = self._call_gemini_json(prompt, timeout=10)
        if res:
            is_incong = bool(res.get("is_incongruent", False))
            score = float(res.get("congruence_score", 5.0))
            if score < 5.0:
                is_incong = True
            return is_incong, score, str(res.get("rationale", ""))

        from app.services.media.entity_detector import _is_incongruent_stock_heuristic
        heur_incong = _is_incongruent_stock_heuristic(media_desc, topic=topic, narration=narration)
        return heur_incong, (3.0 if heur_incong else 7.0), "Heuristic fallback"

    @staticmethod
    def _inspect_candidate_sharpness(cand: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
        """Đo trực tiếp độ sắc nét biên cạnh, độ phân giải & trích xuất thumbnail base64 để Gemini Vision chấm điểm."""
        import base64 as _b64
        import io as _io
        import requests
        from PIL import Image, ImageFilter, ImageStat

        urls = [u for u in (cand.get("url"), cand.get("fallback_url"), cand.get("thumbnail")) if u]
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        for target_u in urls:
            try:
                headers = {"User-Agent": ua, "Accept": "image/*,*/*;q=0.8"}
                if cand.get("page_url"):
                    headers["Referer"] = str(cand["page_url"])
                res = requests.get(target_u, timeout=timeout, headers=headers)
                if res.status_code != 200 or not res.content or len(res.content) < 2500:
                    continue
                with Image.open(_io.BytesIO(res.content)) as im:
                    w, h = im.size
                    if min(w, h) < 540 or max(w / max(h, 1), h / max(w, 1)) > 2.6:
                        continue
                    gray = im.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                    g_stat = ImageStat.Stat(gray)
                    std_con = float(g_stat.stddev[0])
                    lum_mean = float(g_stat.mean[0])
                    if std_con < 18.0 or lum_mean < 24.0 or lum_mean > 228.0:
                        continue
                    edges = gray.filter(ImageFilter.FIND_EDGES)
                    sharpness = float(ImageStat.Stat(edges).mean[0])
                    from app.services.video_memory_rag import VideoMemoryRAG
                    has_bad_text, _ = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(res.content)
                    if sharpness < 5.8 or has_bad_text:
                        continue
                    rgb_thumb = im.convert("RGB") if im.mode != "RGB" else im.copy()
                    rgb_thumb.thumbnail((320, 320), Image.Resampling.BILINEAR)
                    buf = _io.BytesIO()
                    rgb_thumb.save(buf, format="JPEG", quality=72)
                    thumb_b64 = _b64.b64encode(buf.getvalue()).decode("utf-8")
                    return {
                        "width": w,
                        "height": h,
                        "sharpness": round(sharpness, 2),
                        "contrast": round(std_con, 2),
                        "verified_url": target_u,
                        "thumb_b64": thumb_b64,
                    }
            except Exception:
                continue
        return None

    def curate_candidate_photos(
        self,
        candidates: List[Dict[str, Any]],
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
        visual_keyword: str = "",
        used_urls: Optional[set] = None,
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """
        Tuyển chọn ảnh kết hợp kiểm định kỹ thuật (Độ nét, Độ phân giải, Không chữ)
        và kiểm duyệt thị giác đa phương thức Gemini Vision bám sát ý nghĩa kịch bản người dùng.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx

        if is_scene_cancelled_ctx(min_remaining_sec=1.5):
            return None, 0.0, "Scene deadline reached before photo curation.", False

        used = used_urls or set()
        valid = [c for c in candidates if c.get("url") and c.get("url") not in used]
        if not valid:
            return None, 0.0, "Không có ứng viên ảnh nào khả dụng.", True

        top_pool = valid[:6]
        metrics_map: Dict[int, Optional[Dict[str, Any]]] = {}
        ex = ThreadPoolExecutor(max_workers=min(6, len(top_pool)))
        try:
            futs = {ex.submit(self._inspect_candidate_sharpness, cand): i for i, cand in enumerate(top_pool)}
            for fut in as_completed(futs, timeout=5.0):
                if is_scene_cancelled_ctx(min_remaining_sec=1.2):
                    break
                i = futs[fut]
                try:
                    metrics_map[i] = fut.result(timeout=0.05)
                except Exception:
                    metrics_map[i] = None
        except Exception:
            pass
        finally:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                ex.shutdown(wait=False)
        metrics_list = [metrics_map.get(i) for i in range(len(top_pool))]

        scored_sharp: List[Tuple[float, int, Dict[str, Any]]] = []
        for idx, (cand, m) in enumerate(zip(top_pool, metrics_list)):
            if not m:
                continue
            w, h = m["width"], m["height"]
            sharpness = m["sharpness"]
            g_rank = int(cand.get("_google_rank") or 999)
            h_score = float(cand.get("_hscore") or 7.0)

            res_bonus = 1.4 if min(w, h) >= 1000 else (1.0 if min(w, h) >= 720 else 0.4)
            sharp_bonus = min(2.4, sharpness / 10.0)
            rank_bonus = 1.5 if g_rank <= 3 else (1.1 if g_rank <= 8 else (0.6 if g_rank <= 15 else 0.0))
            total_score = min(9.9, round(4.8 + res_bonus + sharp_bonus + rank_bonus + (h_score * 0.12), 2))
            scored_sharp.append((total_score, -idx, {**cand, "_sharp_metrics": m}))

        if scored_sharp:
            scored_sharp.sort(reverse=True)
            va = visual_analysis or {}
            subject_req = va.get("subject") or va.get("core_visual_entity") or visual_keyword or ""
            topic_ctx = topic or va.get("topic") or subject_req
            expected_vis = va.get("visual_metaphor") or visual_keyword or narration
            subtext = va.get("narrative_subtext") or ""

            vision_images: List[Tuple[str, str]] = []
            vision_meta: List[Dict[str, Any]] = []
            for v_idx, (_, _, item_c) in enumerate(scored_sharp[:5]):
                sm = item_c.get("_sharp_metrics") or {}
                b64_img = sm.get("thumb_b64")
                if b64_img:
                    try:
                        import base64 as _b64
                        import io as _io
                        from PIL import Image, ImageDraw
                        raw_bytes = _b64.b64decode(b64_img)
                        with Image.open(_io.BytesIO(raw_bytes)) as pil_im:
                            pil_im = pil_im.convert("RGB")
                            draw = ImageDraw.Draw(pil_im)
                            draw.rectangle([(0, 0), (pil_im.width, 24)], fill=(0, 0, 0))
                            draw.text((6, 5), f"INDEX {v_idx}", fill=(255, 255, 0))
                            out_buf = _io.BytesIO()
                            pil_im.save(out_buf, format="JPEG", quality=75)
                            b64_img = _b64.b64encode(out_buf.getvalue()).decode("utf-8")
                    except Exception:
                        pass
                    vision_images.append(("image/jpeg", b64_img))
                    vision_meta.append({
                        "index": v_idx,
                        "attached_image": f"INDEX {v_idx}",
                        "url": item_c.get("url"),
                        "title_or_alt": str(item_c.get("title") or "")[:100],
                        "resolution": f"{sm.get('width')}x{sm.get('height')}",
                        "sharpness": sm.get("sharpness"),
                    })

            selected_idx = 0
            vision_rationale = ""
            if vision_images and self.rotator:
                paradigm_str = str(va.get("visual_paradigm") or "REAL_WORLD_ACTION").upper()
                photo_prompt = f"""Bạn là Tổng Đạo Diễn Hình Ảnh & Giám Khảo Tuyển Chọn Ảnh Đa Phương Thức (Multimodal Script-Intent Photo Director) cho YouTube Shorts.
Hãy NHÌN TRỰC TIẾP vào {len(vision_images)} bức ảnh ứng viên đính kèm (đã in nhãn "INDEX 0", "INDEX 1"... ở góc trên) và đối chiếu sâu với Ý NGHĨA KỊCH BẢN CỦA NGƯỜI DÙNG để chọn ra bức ảnh xuất sắc nhất.

NGỮ CẢNH KỊCH BẢN CỦA NGƯỜI DÙNG:
- Chủ đề câu chuyện (Topic): "{topic_ctx}"
- Chủ thể đã giải mã của cảnh (Resolved Subject): "{subject_req}"
- Lời thoại phân cảnh (Narration): "{narration}"
- Hàm ý trong mạch truyện (Narrative Subtext): "{subtext}"
- Hình ảnh kỳ vọng: "{expected_vis}" (Trường phái: "{paradigm_str}")

DANH SÁCH ỨNG VIÊN:
{json.dumps(vision_meta, ensure_ascii=False, indent=2)}

TIÊU CHUẨN SUY LUẬN & TUYỂN CHỌN THÔNG MINH:
1. ĐỌC ĐÚNG NHÃN "INDEX X" TRÊN ẢNH: `winner_index` BẮT BUỘC khớp chính xác với nhãn `INDEX X` in trên ảnh và trường `"index"` trong JSON.
2. THẤU HIỂU NGỮ CẢNH KỊCH BẢN (TRÁNH BẪY TỪ KHÓA):
   - Bức ảnh phải truyền tải đúng chủ thể thực sự, hành động và cảm xúc của phân cảnh `{narration}` khi đặt trong câu chuyện `{topic_ctx}`.
   - Loại bỏ ngay ảnh chỉ trùng từ khóa bề mặt nhưng lệch nghĩa kịch bản (ví dụ: đồ chơi/thú nhồi bông, thú cưng trong nhà khi kịch bản nói về thiên nhiên hoang dã, động vật hoặc biển hiệu cửa hàng lọt vào kịch bản tâm lý học con người, hoặc ảnh chụp độc 1 nhãn cầu mắt rời rạc).
3. ĐỒNG NHẤT MÔI TRƯỜNG SINH THÁI / MÙA / BỐI CẢNH THỜI ĐẠI:
   - Môi trường tự nhiên, mùa và thời đại trong ảnh phải ăn khớp với thế giới của kịch bản (loại bỏ ảnh sai mùa/sai hệ sinh thái hoặc thú nhốt trong chuồng bê tông/lồng sắt sở thú).
4. KHUNG HÌNH NGUYÊN BẢN SẠCH 100%:
   - Tuyệt đối loại bỏ ảnh ghép chia đôi/chia ba khung hình (split-screen collage), ảnh có chữ chèn đè (text overlay, meme, quote card), biểu đồ/bản đồ, hoặc logo/watermark.
5. Nếu KHÔNG CÓ bức ảnh nào thực sự truyền tải đúng ý kịch bản (`confidence_score < 6.8`), BẮT BUỘC trả về `"winner_index": -1` và `"should_fallback_to_ai": true`.

TRẢ VỀ JSON DUY NHẤT:
{{
  "winner_index": 0,
  "winner_url": "url_cua_ung_vien_thang_cuoc",
  "confidence_score": 8.8,
  "approved_alternate_indices": [],
  "rationale": "Giải thích ngắn gọn vì sao bức ảnh INDEX X thể hiện sát nhất ý nghĩa kịch bản của người dùng",
  "should_fallback_to_ai": false
}}"""
                photo_dec = self._call_gemini_json(photo_prompt, images=vision_images, timeout=9)
                if photo_dec is not None:
                    w_idx = int(photo_dec.get("winner_index", -1))
                    w_url = str(photo_dec.get("winner_url") or "").strip()
                    v_score = float(photo_dec.get("confidence_score", 0.0))
                    v_fb = bool(photo_dec.get("should_fallback_to_ai", False))
                    v_rat = str(photo_dec.get("rationale", "")).strip()
                    if w_url:
                        for c_i, (_, _, c_obj) in enumerate(scored_sharp[:5]):
                            if c_obj.get("url") == w_url and c_i != w_idx:
                                w_idx = c_i
                                break
                    if 0 <= w_idx < len(scored_sharp) and v_score >= 6.8 and not v_fb:
                        selected_idx = w_idx
                        vision_rationale = v_rat
                        approved_alts = [
                            int(i) for i in (photo_dec.get("approved_alternate_indices") or [])
                            if isinstance(i, (int, float)) and 0 <= int(i) < len(scored_sharp) and int(i) != selected_idx
                        ]
                        scored_sharp[selected_idx][2]["_approved_alt_indices"] = approved_alts
                    else:
                        logger.info(
                            f"🚫 [PhotoVisionAudit] LLM từ chối toàn bộ {len(vision_images)} ảnh ứng viên "
                            f"(Score={v_score}/10: {v_rat}) -> Chuyển truy vấn tiếp theo / AI!"
                        )
                        return None, v_score, v_rat or "Ảnh ứng viên không khớp bối cảnh kịch bản", True
                else:
                    logger.warning("⚠️ [PhotoVisionAudit] Không thể kết nối Gemini Vision -> Từ chối chọn mù ảnh ứng viên chưa kiểm duyệt!")
                    return None, 0.0, "Gemini Vision unavailable — rejected unverified photo candidates", True

            best_score, _, winner_item = scored_sharp[selected_idx]
            m = winner_item.pop("_sharp_metrics", {})
            if m.get("verified_url"):
                if winner_item.get("url") and winner_item["url"] != m["verified_url"] and not winner_item.get("fallback_url"):
                    winner_item["fallback_url"] = winner_item["url"]
                winner_item["url"] = m["verified_url"]
            approved_alt_idxs = winner_item.pop("_approved_alt_indices", None) or []
            alt_urls = [
                scored_sharp[i][2].get("url")
                for i in approved_alt_idxs
                if 0 <= i < len(scored_sharp) and scored_sharp[i][2].get("url") and scored_sharp[i][2].get("url") != winner_item.get("url")
            ]
            for _, _, item_cleanup in scored_sharp:
                item_cleanup.pop("_sharp_metrics", None)
                item_cleanup.pop("_approved_alt_indices", None)
            winner_item["_vision_alternates"] = alt_urls
            g_rank = int(winner_item.get("_google_rank") or 999)
            rank_str = f"Top #{g_rank} Google Images" if g_rank < 900 else winner_item.get("source", "web")
            rationale = (
                f"[{rank_str} · Vision+Sharpness] {vision_rationale + ' | ' if vision_rationale else ''}"
                f"Độ nét={m.get('sharpness')}, Độ phân giải={m.get('width')}x{m.get('height')}px"
            )
            winner_item["curation_score"] = best_score
            winner_item["curation_rationale"] = rationale
            logger.info(f"📸 [PhotoVisionCuration] ✅ Chọn ảnh {rank_str} | Điểm: {best_score}/10 | {rationale}")
            return winner_item, best_score, rationale, False

        return None, 0.0, "Không có ứng viên ảnh nào đạt chuẩn độ nét (>=5.8) và sạch watermark", True
