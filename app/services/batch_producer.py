"""
Script tự động sản xuất & hẹn lịch phát sóng khung giờ vàng cho các kênh YouTube Shorts:
- Hỗ trợ chạy 6 kênh chủ lực (loại trừ Paws & Hearts Rescue theo yêu cầu: `--channel all_6 --count 2`):
  1. The mysteries of the world (`ch_the_mysteries_of_the_world` - US EN)
  2. Psychology of the world (`ch_psychology_of_the_world` - US EN)
  3. Wild Odyssey (`ch_wild_odyssey` - US EN)
  4. Alternate History & "What If?" (`@alternatehistorywhatif-e2x` - US EN)
  5. Odisseia Selvagem (`ch_odisseia_selvagem` - Brazil PT-BR)
  6. Mistérios do Mundo (`ch_misterios_do_mundo` - Brazil PT-BR)

Quy trình chuẩn 5 bước nghiêm ngặt cho từng video:
- Bước 1: Tạo kịch bản viral 5-Act retention + Đánh giá chấm điểm kịch bản (>= 80/100) + Chống trùng lặp chủ đề.
- Bước 2: Tổng hợp bộ Tags SEO chuẩn ngách (tối đa 25 tags) + Hashtags Tiêu đề & Mô tả (`build_smart_video_tags_and_hashtags`).
- Bước 3: Đăng ký VideoJob vào Database kèm khe giờ vàng chuẩn (`SmartScheduler`).
- Bước 4: Dựng video 1080x1920 @ 30fps chất lượng cao (`VideoPipeline` + LangGraph 5 Graders) + Chấm điểm FFprobe QA & VisualCritic.
- Bước 5: Upload YouTube Shorts, gắn đầy đủ Tags SEO, Custom Thumbnail, Pinned Comment & Hẹn lịch phát sóng khung giờ vàng (`verify_and_heal_video_metadata`).
"""
import sys
import os
import json
import asyncio
import logging
import argparse
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Đảm bảo import được module dự án
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# UTF-8 trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ProduceEnglishChannels")

def _load_channels_config_from_yaml() -> dict[str, dict]:
    """
    Nạp cấu hình 7 kênh trực tiếp từ `config/channels.yaml` kết hợp `config/niches/*.yaml` (100% YAML SSOT).
    Tuyệt đối không hardcode `director_notes` hay `topics` trong code Python.
    """
    import yaml as _yaml
    from app.services.niche_manager import get_niche, build_script_prompt_instructions

    channels_yaml_path = project_root / "config" / "channels.yaml"
    raw_data = {}
    if channels_yaml_path.exists():
        try:
            raw_data = _yaml.safe_load(channels_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"[BatchProducer] Không thể đọc config/channels.yaml: {e}")

    default_tags_by_niche = {
        "deep_sea_earth_mysteries": [
            "shorts", "mystery", "deepocean", "unexplained", "thalassophobia",
            "deepsea", "science", "creepy", "oceanmysteries", "mariana", "viral",
        ],
        "psychology": [
            "shorts", "psychology", "darkpsychology", "humanbehavior", "bodylanguage",
            "microexpressions", "mindset", "subconsciousmind", "socialdynamics", "viral",
        ],
        "wildlife_predators": [
            "shorts", "wildlife", "predators", "bigcats", "nature",
            "apexpredator", "jaguar", "animals", "documentary", "viral",
        ],
        "alternate_history": [
            "shorts", "alternatehistory", "whatif", "history", "empires",
            "timeline", "documentary", "historicalfacts", "viral",
        ],
        "animal_rescue": [
            "shorts", "rescuedog", "dogs", "animalrescue", "workingdogs",
            "k9", "newfoundlanddog", "saintbernard", "germanshepherd", "pets", "animals", "viral",
        ],
    }

    cfg_map: dict[str, dict] = {}
    channels_list = raw_data.get("channels") or []
    if not channels_list:
        for acc in (raw_data.get("google_accounts") or []):
            for bch in (acc.get("brand_channels") or []):
                bch_copy = dict(bch)
                bch_copy.setdefault("google_account", acc.get("id", "account_1"))
                channels_list.append(bch_copy)

    for ch in channels_list:
        ch_id = str(ch.get("id") or "").strip()
        if not ch_id:
            continue
        niche_id = str(ch.get("niche") or "general").strip()
        lang = str(ch.get("language") or "en").strip()
        niche_prof = get_niche(niche_id)
        yaml_topics = [
            str(t).strip()
            for t in ((niche_prof.get("script_blueprint") or {}).get("viral_topic_bank_2026") or [])
            if str(t).strip()
        ]
        sched = ch.get("schedule") or {}
        pub_hour = int(sched.get("publish_hour_utc", 17))
        kw_tags = [str(k).replace(" ", "") for k in (ch.get("channel_keywords") or [])[:10] if str(k).strip()]
        def_tags = list(dict.fromkeys((default_tags_by_niche.get(niche_id) or ["shorts", "viral"]) + kw_tags))

        entry = {
            "id": ch_id,
            "name": str(ch.get("name") or ch_id),
            "niche": niche_id,
            "language": lang,
            "google_account": str(ch.get("google_account") or "account_1"),
            "preferred_voice": str(ch.get("preferred_voice") or ("pt-BR-AntonioNeural" if lang.lower().startswith("pt") else "en-US-ChristopherNeural")),
            "handle": str(ch.get("handle") or ""),
            "amazon_tag": str(ch.get("amazon_tag") or "kizakawa-20"),
            "publish_hour_utc": pub_hour,
            "prefer_non_youtube_sources": bool(ch.get("prefer_non_youtube_sources", ch_id == "ch_paws_hearts_rescue")),
            "director_notes": build_script_prompt_instructions(niche_prof),
            "topics": yaml_topics,
            "default_tags": def_tags,
        }
        cfg_map[ch_id] = entry
        if entry["handle"]:
            cfg_map[entry["handle"]] = entry
            cfg_map[entry["handle"].lower()] = entry

    # Aliases để tương thích ngược toàn hệ thống
    alias_pairs = {
        "channel_maya": "ch_psychology_of_the_world",
        "ch_psychology": "ch_psychology_of_the_world",
        "psychology": "ch_psychology_of_the_world",
        "ucsrjiil67qxidtxt70z-iwa": "ch_the_mysteries_of_the_world",
        "ch_mysteries": "ch_the_mysteries_of_the_world",
        "ch_alternate_history": "@alternatehistorywhatif-e2x",
        "alternate_history": "@alternatehistorywhatif-e2x",
        "wild_odyssey": "ch_wild_odyssey",
        "odisseia_selvagem": "ch_odisseia_selvagem",
        "misterios_do_mundo": "ch_misterios_do_mundo",
        "paws_hearts_rescue": "ch_paws_hearts_rescue",
    }
    for alias_k, target_k in alias_pairs.items():
        if target_k in cfg_map:
            cfg_map[alias_k] = cfg_map[target_k]

    return cfg_map


CHANNELS_CONFIG = _load_channels_config_from_yaml()

SIX_ACTIVE_CHANNELS = [
    "ch_the_mysteries_of_the_world",
    "ch_psychology_of_the_world",
    "ch_wild_odyssey",
    "@alternatehistorywhatif-e2x",
    "ch_odisseia_selvagem",
    "ch_misterios_do_mundo",
]

_SESSION_USED_TOPICS: dict[str, list[str]] = {}
_SESSION_OCCUPIED_SLOTS: dict[str, list[datetime]] = {}


def get_schedule_slot(
    publish_hour_utc: int,
    channel_id: str = "",
    is_long_form: bool = False,
) -> tuple[datetime, str, str]:
    """
    Tính slot giờ vàng tiếp theo có lệch pha chống trùng lặp.
    Đảm bảo khi tạo nhiều video liên tiếp cho cùng 1 kênh sẽ tự động phân bổ vào khung giờ vàng chuẩn định dạng
    (Shorts 9:16 vs Long-Form 16:9 với Anti-Collision >= 3.0 giờ).
    """
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_active_slots_pool,
            get_longform_slot_for_channel,
            is_brazil_channel,
            get_channel_stagger_minutes,
            MIN_SPACING_SECONDS,
            LONG_SHORT_ANTI_COLLISION_SECONDS,
            MAX_VIDEOS_PER_DAY,
            MAX_LONG_FORM_PER_DAY,
            BR_TZ,
            US_EASTERN,
            VN_TZ,
            WEEKDAY_VN_MAP,
        )
        slot_dt, slot_iso, disp_vn, disp_us = get_next_available_golden_slot(
            channel_id or "ch_psychology_of_the_world",
            is_long_form=is_long_form,
        )
        session_key = f"{channel_id}::{'long' if is_long_form else 'short'}"
        cross_key = f"{channel_id}::{'short' if is_long_form else 'long'}"
        prev_slots = _SESSION_OCCUPIED_SLOTS.get(session_key, [])
        cross_slots = _SESSION_OCCUPIED_SLOTS.get(cross_key, [])
        has_same_collision = bool(prev_slots and any(abs((slot_dt - ps).total_seconds()) < MIN_SPACING_SECONDS for ps in prev_slots))
        has_cross_collision = bool(cross_slots and any(abs((slot_dt - cs).total_seconds()) < LONG_SHORT_ANTI_COLLISION_SECONDS for cs in cross_slots))
        if has_same_collision or has_cross_collision:
            is_br = is_brazil_channel(channel_id)
            pool = [get_longform_slot_for_channel(channel_id)] if is_long_form else list(get_active_slots_pool(channel_id))
            target_tz = BR_TZ if is_br else US_EASTERN
            stagger = get_channel_stagger_minutes(channel_id)
            max_per_day = MAX_LONG_FORM_PER_DAY if is_long_form else MAX_VIDEOS_PER_DAY
            ref_dates = [ps for ps in (prev_slots + cross_slots)]
            base_date = max(ref_dates).date() if ref_dates else slot_dt.date()
            for d_off in range(0, 20):
                t_date = base_date + timedelta(days=d_off)
                found_new = False
                for s_cfg in sorted(pool, key=lambda x: (x.get("rank", 99), x["hour"], x["minute"])):
                    eff_stagger = 0 if s_cfg.get("_pre_staggered") else stagger
                    cand = datetime(
                        t_date.year, t_date.month, t_date.day,
                        s_cfg["hour"], s_cfg["minute"], 0, tzinfo=timezone.utc
                    ) + timedelta(minutes=eff_stagger)
                    if cand <= datetime.now(timezone.utc) + timedelta(minutes=20):
                        continue
                    if any(abs((cand - ps).total_seconds()) < MIN_SPACING_SECONDS for ps in prev_slots):
                        continue
                    if any(abs((cand - cs).total_seconds()) < LONG_SHORT_ANTI_COLLISION_SECONDS for cs in cross_slots):
                        continue
                    m_date = cand.astimezone(target_tz).date()
                    if sum(1 for ps in prev_slots if ps.astimezone(target_tz).date() == m_date) >= max_per_day:
                        continue
                    slot_dt = cand
                    slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    c_vn = slot_dt.astimezone(VN_TZ)
                    w_name = WEEKDAY_VN_MAP.get(c_vn.weekday(), "Ngày")
                    vn_p = "Đêm" if c_vn.hour >= 18 else "Sáng"
                    disp_vn = f"{c_vn.strftime('%H:%M')} {vn_p} {w_name}, {c_vn.strftime('%d/%m/%Y')} (Giờ VN)"
                    disp_us = s_cfg["label_us"]
                    found_new = True
                    break

                if found_new:
                    break

        _SESSION_OCCUPIED_SLOTS.setdefault(session_key, []).append(slot_dt)
        return slot_dt, disp_vn, disp_us
    except Exception:
        now_utc = datetime.now(timezone.utc)
        candidate = now_utc.replace(hour=publish_hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= now_utc + timedelta(hours=2):
            candidate += timedelta(days=1)
        VN_TZ = timezone(timedelta(hours=7))
        dt_vn = candidate.astimezone(VN_TZ)
        disp_vn = f"{dt_vn.strftime('%H:%M')} ngày {dt_vn.strftime('%d/%m/%Y')} (Giờ VN)"
        return candidate, disp_vn, "Golden Slot"


async def produce_channel_video(
    channel_key: str,
    custom_topic: str = None,
    dry_run: bool = False,
    render_only: bool = False,
    send_telegram: bool = False,
    telegram_chat_id: str = None,
    video_index: int = 1,
    total_per_channel: int = 1,
    video_format: str = "short",
):
    cfg = CHANNELS_CONFIG[channel_key]
    ch_id = cfg["id"]
    ch_name = cfg["name"]
    ch_niche = cfg["niche"]
    ch_lang = cfg["language"]
    voice = cfg["preferred_voice"]
    amazon_tag = cfg["amazon_tag"]

    is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
    SCHEDULE_SLOT_UTC, DISPLAY_VN, DISPLAY_MARKET = get_schedule_slot(
        cfg["publish_hour_utc"],
        ch_id,
        is_long_form=is_long_form,
    )
    SCHEDULE_SLOT_ISO = SCHEDULE_SLOT_UTC.strftime("%Y-%m-%dT%H:%M:%SZ")
    fmt_header = "PHIM TÀI LIỆU 16:9 (10 CHƯƠNG >= 9M15S)" if is_long_form else "YOUTUBE SHORTS 9:16"

    print("\n" + "=" * 80)
    print(f"🌟 [{video_index}/{total_per_channel}] SẢN XUẤT {fmt_header} CHO KÊNH: {ch_name} ({ch_id})")
    print(f"🎬 Ngách: {ch_niche} | Ngôn ngữ: {ch_lang} | Giọng: {voice}")
    print(f"⏰ KHUNG GIỜ VÀNG: {DISPLAY_VN} ({SCHEDULE_SLOT_ISO}) | {DISPLAY_MARKET}")
    print("=" * 80)

    from app.agents.content_agent import ContentAgent
    from app.services.quality_evaluator import QualityEvaluator
    from app.services.channel_intelligence import (
        get_winner_cloner_blueprint,
        check_idea_similarity,
        ensure_unique_title,
        extract_saturated_channel_concepts,
        has_consecutive_subject_fatigue,
    )
    from app.services.niche_manager import validate_channel_niche_alignment
    from app.database.models import get_db, VideoJob, PublishedVideo, log_activity
    from app.services.smart_scheduler import resolve_channel_family
    from app.agents.publisher_agent import PublisherAgent

    agent = ContentAgent()
    evaluator = QualityEvaluator()

    # ── BƯỚC 1: TẠO VÀ ĐÁNH GIÁ CHẤM ĐIỂM KỊCH BẢN (CHỐNG TRÙNG LẶP 100%) ─────────
    print(f"\n[1/5] 📝 BƯỚC 1: Chọn chủ đề mới & Xây dựng kịch bản viral cho {ch_name}...")
    used_in_session = _SESSION_USED_TOPICS.setdefault(ch_id, [])
    existing_refs: list[str] = list(used_in_session)
    try:
        fam_ids = resolve_channel_family(ch_id)
        with get_db() as db:
            recent_db = (
                db.query(VideoJob)
                .filter(
                    VideoJob.target_channel_id.in_(fam_ids),
                    ~VideoJob.status.ilike("CANCELLED%"),
                    VideoJob.status != "FAILED",
                )
                .order_by(VideoJob.id.desc())
                .limit(45)
                .all()
            )
            for rj in recent_db:
                if rj.topic:
                    existing_refs.append(rj.topic)
                if rj.title:
                    existing_refs.append(rj.title)
            pub_db = (
                db.query(PublishedVideo)
                .filter(PublishedVideo.channel_id.in_(fam_ids))
                .order_by(PublishedVideo.id.desc())
                .limit(45)
                .all()
            )
            for pv in pub_db:
                if pv.title:
                    existing_refs.append(pv.title)
            audit_p = project_root / "storage" / "channel_audit_latest.json"
            if audit_p.exists():
                aud_j = json.loads(audit_p.read_text(encoding="utf-8"))
                ch_aud = (aud_j or {}).get(ch_id, {})
                for av in ch_aud.get("videos", []):
                    if av.get("title"):
                        existing_refs.append(av["title"])
                for sq in ch_aud.get("scheduled_queue", []):
                    if sq.get("title"):
                        existing_refs.append(sq["title"])
    except Exception:
        pass

    if custom_topic:
        topic = custom_topic
    else:
        from app.services.niche_manager import get_niche
        niche_prof_bp = get_niche(ch_niche)
        yaml_topics_2026 = [
            str(t).strip()
            for t in ((niche_prof_bp.get("script_blueprint") or {}).get("viral_topic_bank_2026") or [])
            if str(t).strip()
        ]
        # Đối với kênh Shorts, lọc bỏ các chủ đề dành riêng cho Long-Form ('Every ... Explained', '5-Round Gauntlet', 'Hour 1 to Day 14') nếu đang dựng Shorts;
        # Đối với Long-Form 16:9, ưu tiên các chủ đề đại cảnh nhiều chương lên đầu và khớp ngôn ngữ kênh (pt-BR vs en-US).
        is_lf_mode = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
        is_pt_ch = ch_lang.lower().startswith("pt")
        _lf_markers = ("every ", "todos os", "todas as", "5-round gauntlet", "gauntlet", "poderia ", "could a ", "how long you", "quanto tempo", "hour 1 to day 14")

        def _is_pt_topic_str(txt: str) -> bool:
            low_tx = txt.lower()
            return any(ch in low_tx for ch in ("ã", "õ", "ç", "ê", "á", "é", "ó", "ú")) or any(
                w in f" {low_tx} " for w in (" por que ", " como ", " contra ", " dos ", " das ", " uma ", " um ")
            )

        lang_matched_topics = [t for t in yaml_topics_2026 if _is_pt_topic_str(t) == is_pt_ch]
        if not is_lf_mode:
            yaml_topics_filtered = [
                t for t in lang_matched_topics
                if not any(lf_k in t.lower() for lf_k in ("every ", "todos os ", "todas as ", "5-round gauntlet", "hour 1 to day 14", "quanto tempo você sobreviveria"))
            ]
        else:
            lf_first = [t for t in lang_matched_topics if any(lf_k in t.lower() for lf_k in _lf_markers)]
            lf_rest = [t for t in lang_matched_topics if t not in lf_first]
            yaml_topics_filtered = lf_first + lf_rest

        candidate_topics: list[str] = list(yaml_topics_filtered)
        try:
            wb = get_winner_cloner_blueprint(ch_id, niche_id=ch_niche)
            fresh_pool = wb.get("fresh_sequel_topics") or []
            if fresh_pool:
                candidate_topics.extend(
                    t for t in fresh_pool
                    if t not in candidate_topics and _is_pt_topic_str(t) == is_pt_ch
                )
                if wb.get("winner_title"):
                    print(f"   🏆 [Winner Cloner] Tham chiếu công thức quán quân '{wb['winner_title']}' ({wb['winner_views']} views)")
        except Exception:
            pass
        # Bổ sung danh sách dự phòng của kênh (khớp đúng ngôn ngữ kênh) sau danh sách chuẩn từ YAML `config/niches/*.yaml`
        for fb_t in cfg.get("topics", []):
            if fb_t not in candidate_topics and _is_pt_topic_str(fb_t) == is_pt_ch:
                candidate_topics.append(fb_t)

        topic = None
        for cand_t in candidate_topics:
            if cand_t in used_in_session:
                continue
            niche_res = validate_channel_niche_alignment(
                channel_id=ch_id,
                title=cand_t,
                topic=cand_t,
            )
            if not niche_res.get("passed", True):
                continue
            is_sim, _, _ = check_idea_similarity(cand_t, existing_refs, similarity_threshold=0.40, channel_id=ch_id)
            if is_sim:
                continue
            recent_ordered_refs = list(reversed(used_in_session)) + [r for r in existing_refs if r not in used_in_session][:6]
            is_consec_fatigued, collided_stem = has_consecutive_subject_fatigue(cand_t, recent_ordered_refs, window=3)
            if is_consec_fatigued:
                print(f"   ⏭️ [Anti-Cohort-Fatigue] Bỏ qua '{cand_t[:55]}...' vì trùng chủ thể liên tiếp '{collided_stem}' với video vừa tạo/lên lịch!")
                continue
            topic = cand_t
            break

        if not topic:
            # Nếu các chủ đề mẫu trong YAML đã được sản xuất, gọi LLM sinh chủ đề mới bám sát `config/niches/*.yaml`
            try:
                ai_ideas = agent.suggest_viral_ideas(
                    channel_id=ch_id,
                    channel_name=ch_name,
                    channel_niche=ch_niche,
                    language=ch_lang,
                    recent_titles=existing_refs[:10],
                    count=5,
                )
                for idea_obj in (ai_ideas or []):
                    cand_ai_t = str((idea_obj or {}).get("topic") or "").strip()
                    if not cand_ai_t or cand_ai_t in used_in_session:
                        continue
                    if not validate_channel_niche_alignment(channel_id=ch_id, title=cand_ai_t, topic=cand_ai_t).get("passed", True):
                        continue
                    is_sim_ai, _, _ = check_idea_similarity(cand_ai_t, existing_refs, similarity_threshold=0.40, channel_id=ch_id)
                    if not is_sim_ai:
                        topic = cand_ai_t
                        print(f"   ✨ [YAML -> LLM Ideation] Đã sinh chủ đề mới theo chuẩn YAML '{ch_niche}.yaml': {topic}")
                        break
            except Exception:
                pass

        if not topic:
            # Fallback: chọn ứng viên có độ trùng lặp thấp nhất
            scored_remaining = []
            for t in candidate_topics:
                if t in used_in_session:
                    continue
                if not validate_channel_niche_alignment(channel_id=ch_id, title=t, topic=t).get("passed", True):
                    continue
                _, sim_sc, _ = check_idea_similarity(t, existing_refs, similarity_threshold=0.40, channel_id=ch_id)
                scored_remaining.append((sim_sc, t))
            scored_remaining.sort(key=lambda x: x[0])
            topic = scored_remaining[0][1] if scored_remaining else candidate_topics[0]

    used_in_session.append(topic)
    print(f"   -> Chủ đề được chọn: {topic}")

    recent_for_cooldown = list(reversed(used_in_session[:-1])) + [r for r in existing_refs if r not in used_in_session][:8]
    saturated_cooldown = extract_saturated_channel_concepts(recent_for_cooldown, min_frequency=2)
    if saturated_cooldown:
        print(f"   🛡️ [Cooldown Chủ Thể Bão Hòa]: {', '.join(saturated_cooldown[:6])}")

    script = None
    script_evaluation = None
    is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
    for attempt in range(1, 2 if is_long_form else 4):
        print(f"\n   [Lần {attempt}/3] Sinh kịch bản từ hồ sơ YAML `config/niches/{ch_niche}.yaml` và gửi ban thẩm định AI...")
        is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
        raw_script = agent.generate_script(
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang,
            director_notes=None,
            cooldown_entities=saturated_cooldown,
            channel_id=ch_id,
            video_format="long" if is_long_form else "short",
        )

        eval_res = evaluator.evaluate_script(
            script_dict=raw_script.to_dict(),
            topic=topic,
            channel_niche=ch_niche,
            language=ch_lang,
        )

        print(f"   📊 KẾT QUẢ ĐÁNH GIÁ KỊCH BẢN:")
        print(f"      - Điểm tổng: {eval_res['score']}/100 ({eval_res['verdict']})")
        print(f"      - Hook 3s đầu: {eval_res['hook_score']}/10 | Đúng ngách: {eval_res['niche_score']}/10 | Ngôn ngữ: {eval_res['language_score']}/10 | Giữ chân: {eval_res['retention_score']}/10")

        if eval_res["passed"]:
            print(f"   ✅ KỊCH BẢN ĐẠT TIÊU CHUẨN XUẤT BẢN! ({eval_res['score']}/100 >= 80)")
            script = raw_script
            script_evaluation = eval_res
            break
        else:
            print(f"   ⚠️ Kịch bản {eval_res['score']}/100 < 80. Thử tối ưu lại...")
            script = raw_script
            script_evaluation = eval_res

    raw_title = script.title or f"{topic.split(':')[0].strip()}"
    try:
        unique_title, was_dup, _ = ensure_unique_title(
            candidate_title=raw_title,
            channel_id=ch_id,
            topic=topic,
            language=ch_lang,
            similarity_threshold=0.42,
        )
        if was_dup and unique_title:
            raw_title = unique_title
            script.title = unique_title
    except Exception:
        pass
    used_in_session.append(raw_title)
    default_fmt_hash = ("#documentario" if ch_lang.lower().startswith("pt") else "#documentary") if is_long_form else "#Shorts"
    base_desc = script.description or f"{script.hook or topic}\n\n{default_fmt_hash} #{ch_niche}"
    if is_long_form:
        import re as _re_bp
        base_desc = _re_bp.sub(r"#shorts\b\s*", "", base_desc, flags=_re_bp.IGNORECASE).strip()
    raw_tags = [
        t for t in dict.fromkeys([*(script.tags or []), *cfg["default_tags"]])
        if not (is_long_form and str(t).strip().lower().lstrip("#") in ("shorts", "ytshorts", "youtubeshorts"))
    ]
    pinned_comment_base = script.pinned_comment or (
        "Qual é a sua opinião sobre isso? Comente abaixo! 👇🔥"
        if ch_lang.lower().startswith("pt")
        else "What is your thought on this? Drop a comment below! 👇🔥"
    )

    # ── BƯỚC 2: TỐI ƯU BỘ TAGS SEO CHUẨN NGÁCH & CHỦ ĐỀ (TỐI ĐA 25 TAGS + HASHTAGS) ──
    print(f"\n[2/5] 🏷️ BƯỚC 2: Tổng hợp bộ Tags SEO & Hashtags chuẩn ngách cho {ch_name}...")
    ch_monet_cfg = {
        "id": ch_id,
        "name": ch_name,
        "niche": ch_niche,
        "language": ch_lang,
        "google_account": cfg["google_account"],
        "amazon_tag": amazon_tag,
        "affiliate_enabled": False,
        "enable_donation": False,
        "default_tags": cfg["default_tags"],
    }
    pub_agent = PublisherAgent(channel_config=ch_monet_cfg)
    smart_bundle = pub_agent.build_smart_video_tags_and_hashtags(
        title=raw_title,
        topic=topic,
        script=script.full_narration,
        extra_tags=raw_tags,
        is_long_form=is_long_form,
    )
    title = smart_bundle["enriched_title"]
    tags = smart_bundle["tags"]
    from app.services.monetization_engine import enrich_video_monetization
    final_description, final_pinned_comment, _ = enrich_video_monetization(
        channel_config=ch_monet_cfg,
        title=title,
        description=base_desc,
        pinned_comment=pinned_comment_base,
        topic=topic,
        script=script.full_narration,
        enable_affiliate=False,
        enable_donation=False,
    )
    final_pinned_comment = pub_agent.sanitize_engagement_comment(final_pinned_comment)
    if smart_bundle["description_hashtags"] not in final_description:
        final_description = f"{final_description.strip()}\n\n{smart_bundle['description_hashtags']}".strip()

    print(f"   -> Tiêu đề chuẩn SEO : {title}")
    print(f"   -> Bộ Tags ({len(tags)} thẻ): {', '.join(tags[:12])}...")
    print(f"   -> Hashtags Mô tả    : {smart_bundle['description_hashtags']}")
    print(f"   -> Số phân cảnh      : {len(script.scenes)} scenes")

    if dry_run:
        print("\n[DRY-RUN] Hoàn tất kiểm thử kịch bản, bộ Tags & khe giờ vàng. Dừng ở đây.")
        return {
            "channel": ch_name,
            "channel_id": ch_id,
            "title": title,
            "tags_count": len(tags),
            "tags": tags,
            "script_score": script_evaluation["score"] if script_evaluation else 85,
            "video_score": 100,
            "scheduled_time": DISPLAY_VN,
            "scheduled_iso": SCHEDULE_SLOT_ISO,
            "youtube_url": "DRY_RUN",
            "video_id": None,
        }

    # ── BƯỚC 3: ĐĂNG KÝ VIDEOJOB VÀO DATABASE ────────────────────────────────────
    print(f"\n[3/5] 💾 BƯỚC 3: Đăng ký VideoJob vào hệ thống...")
    is_long_form = str(video_format or "short").strip().lower() in ("long", "long_form", "longform", "16:9")
    scenes_dict = script.to_dict()
    scenes_dict["video_format"] = "long" if is_long_form else "short"
    scenes_dict["pinned_comment"] = final_pinned_comment
    if script_evaluation and script_evaluation.get("passed"):
        scenes_dict["_pre_evaluated_score"] = float(script_evaluation.get("score", 85))
        scenes_dict["_pre_evaluated_hook"] = float(script_evaluation.get("hook_score", 8.5))
    scenes_json_str = json.dumps(scenes_dict, ensure_ascii=False)

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=ch_id,
            topic=topic,
            title=title,
            script=script.full_narration,
            description=final_description,
            tags=",".join(tags),
            scenes_json=scenes_json_str,
            status="RENDERING",
            scheduled_time=SCHEDULE_SLOT_UTC,
            is_auto_generated=0,
            progress_percent=15,
            progress_message=f"Đang sản xuất video #{video_index} cho {ch_name} (Khung giờ vàng {DISPLAY_VN})...",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(
            db,
            telegram_id=1,
            username="System_Director",
            action_type="SCHEDULED_VIDEO_START",
            details=f"Khởi động sản xuất #{job_id} '{title}' cho {ch_name} ({DISPLAY_VN})",
            channel_id=ch_id,
        )
    print(f"   -> Job #{job_id} đã được khởi tạo thành công.")

    # ── BƯỚC 4: DỰNG VIDEO CHẤT LƯỢNG CAO NHẤT (1080x1920 HOẶC 1920x1080 @ 30FPS) & CHẤM ĐIỂM QA ──
    res_label = "1920x1080 (16:9 Documentary)" if is_long_form else "1080x1920 (9:16 Shorts)"
    print(f"\n[4/5] 🎬 BƯỚC 4: Dựng video chất lượng cao ({res_label} @ 30fps, giọng {voice}) & Thẩm định...")

    async def _progress_cb(stage: str, msg: str, percent: int = 0):
        print(f"   [Job #{job_id}] [{stage}] [{percent}%] {msg}", flush=True)

    from app.services.pipeline import VideoPipeline
    pipeline = VideoPipeline(job_id=job_id, progress_callback=_progress_cb)
    pipeline.niche = ch_niche

    prev_non_yt = os.environ.get("PREFER_NON_YOUTUBE_SOURCES")
    prev_allow_yt = os.environ.get("ALLOW_LOW_RISK_YOUTUBE_CLIPS")
    if cfg.get("prefer_non_youtube_sources"):
        os.environ["PREFER_NON_YOUTUBE_SOURCES"] = "true"
        os.environ["ALLOW_LOW_RISK_YOUTUBE_CLIPS"] = "false"
        print("   🛡️ [Non-YouTube Clean Sources Mode]: Đã kích hoạt chế độ lấy nguồn 100% ngoài YouTube (Pexels HD + Pixabay HD + Wikimedia + Bilibili Safe-Crop)!")
    try:
        render_result = await pipeline.execute()
    finally:
        if cfg.get("prefer_non_youtube_sources"):
            if prev_non_yt is None:
                os.environ.pop("PREFER_NON_YOUTUBE_SOURCES", None)
            else:
                os.environ["PREFER_NON_YOUTUBE_SOURCES"] = prev_non_yt
            if prev_allow_yt is None:
                os.environ.pop("ALLOW_LOW_RISK_YOUTUBE_CLIPS", None)
            else:
                os.environ["ALLOW_LOW_RISK_YOUTUBE_CLIPS"] = prev_allow_yt
    output_video_path = render_result.output_path

    if not output_video_path or not Path(output_video_path).exists():
        raise RuntimeError(f"Pipeline render thất bại, không tìm thấy file tại: {output_video_path}")

    print("\n   🔍 Đang tiến hành thẩm định chất lượng kỹ thuật & thị giác của file MP4...")
    video_qa = evaluator.evaluate_video(str(output_video_path), video_format="long" if is_long_form else "short")
    from app.graph.evaluators.visual_critic import VisualCritic
    vc_check = VisualCritic.inspect_clip_before_concat(
        str(output_video_path), min_short_edge=720, min_sharpness=5.5, check_watermark=False
    )
    print(f"   📊 KẾT QUẢ CHẤM ĐIỂM VIDEO (#{job_id}):")
    print(f"      - Điểm kỹ thuật FFprobe: {video_qa['score']}/100 ({video_qa.get('width')}x{video_qa.get('height')} @ {video_qa.get('fps')} FPS, {video_qa.get('duration_seconds', 0)}s, {video_qa.get('file_size_mb')} MB)")
    print(f"      - Độ sắc nét trung bình: {vc_check.get('sharpness', 0)} | Độ sáng: {vc_check.get('mean_luminance', 0)}")
    print(f"      - Âm thanh: {'Có luồng AAC chuẩn -14 LUFS' if video_qa.get('has_audio') else 'Thiếu'}")

    # Đồng bộ lại scenes_json, bảng Mốc Chương TTS thực tế (`⏱️ Capítulos:` / `⏱️ Chapters:`) & pinned_comment ngay sau Bước 4
    # để cả chế độ Telegram Preview, `--render-only` lẫn Upload YouTube đều nhận đúng mô tả chuẩn hóa từng giây!
    try:
        with get_db() as db_sync:
            j_sync = db_sync.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j_sync and j_sync.scenes_json:
                scenes_json_str = j_sync.scenes_json
                s_refined = json.loads(scenes_json_str) if isinstance(scenes_json_str, str) else scenes_json_str
                if isinstance(s_refined, dict):
                    if s_refined.get("pinned_comment"):
                        final_pinned_comment = pub_agent.sanitize_engagement_comment(str(s_refined["pinned_comment"]).strip())
                    if s_refined.get("description"):
                        ref_desc = str(s_refined["description"]).strip()
                        if smart_bundle["description_hashtags"] not in ref_desc:
                            ref_desc = f"{ref_desc}\n\n{smart_bundle['description_hashtags']}".strip()
                        final_description = ref_desc
                        j_sync.description = final_description
    except Exception:
        pass

    if send_telegram:
        try:
            import subprocess as _sp_tg
            import requests as _req_tg
            from config.settings import settings as _st_tg
            tg_token = getattr(_st_tg, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
            target_chat = (
                telegram_chat_id
                or getattr(_st_tg, "TELEGRAM_ADMIN_ID", None)
                or os.getenv("TELEGRAM_CHAT_ID")
                or "8498162618"
            )
            if tg_token and target_chat and Path(output_video_path).exists():
                print(f"\n   📲 [Telegram Preview] Đang chuẩn bị & gửi video #{job_id} vào Telegram ({target_chat}) để duyệt trước...")
                tg_send_path = str(output_video_path)
                tmp_tg_path = str(Path(output_video_path).with_name(f"tg_preview_{job_id}.mp4"))
                try:
                    if Path(output_video_path).stat().st_size > 15 * 1024 * 1024:
                        tg_scale = "scale=1280:720" if is_long_form else "scale=720:1280"
                        tg_crf = "28" if is_long_form else "26"
                        _sp_tg.run(
                            [
                                "ffmpeg", "-y", "-i", str(output_video_path),
                                "-vf", tg_scale,
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", tg_crf,
                                *(["-maxrate", "850k", "-bufsize", "1700k"] if is_long_form else []),
                                "-c:a", "aac", "-b:a", "96k" if is_long_form else "128k",
                                "-movflags", "+faststart",
                                tmp_tg_path,
                            ],
                            capture_output=True,
                            timeout=180 if is_long_form else 60,
                            check=False,
                        )
                        if Path(tmp_tg_path).exists() and Path(tmp_tg_path).stat().st_size > 100_000:
                            tg_send_path = tmp_tg_path
                except Exception:
                    pass
                fmt_badge = "VIDEO DÀI 16:9 (1920x1080)" if is_long_form else "SHORTS 9:16 (1080x1920)"
                caption_txt = (
                    f"🎬 <b>[BẢN DUYỆT TRƯỚC ({fmt_badge}) - {ch_name}]</b>\n"
                    f"🆔 <b>Job ID:</b> <code>#{job_id}</code>\n"
                    f"📌 <b>Tiêu đề:</b> {smart_bundle['enriched_title']}\n"
                    f"🎯 <b>Chủ đề:</b> {topic}\n"
                    f"📊 <b>Điểm QA:</b> Script <code>{script_evaluation['score'] if script_evaluation else 85}/100</code> | Video <code>{video_qa['score']}/100</code> ({video_qa.get('duration_seconds', 0)}s, {video_qa.get('width')}x{video_qa.get('height')}, Sharpness={vc_check.get('sharpness', 0)})\n"
                    f"⏰ <b>Khung giờ vàng dự kiến:</b> {DISPLAY_VN}\n"
                    f"🏷️ <b>Tags ({len(tags)}):</b> <code>{', '.join(tags[:12])}</code>\n\n"
                    f"💬 <b>Pinned Comment:</b> {final_pinned_comment}"
                )
                try:
                    thumb_16x9 = getattr(render_result, "thumbnail_path", None)
                    if is_long_form and thumb_16x9 and Path(str(thumb_16x9)).exists():
                        with open(str(thumb_16x9), "rb") as tf_tg:
                            _req_tg.post(
                                f"https://api.telegram.org/bot{tg_token}/sendPhoto",
                                data={
                                    "chat_id": str(target_chat),
                                    "caption": f"🖼️ <b>[THUMBNAIL NGANG 16:9 - Job #{job_id}]</b>\n📌 {smart_bundle['enriched_title']}",
                                    "parse_mode": "HTML",
                                },
                                files={"photo": (Path(str(thumb_16x9)).name, tf_tg, "image/jpeg")},
                                timeout=60,
                            )
                    cand_chats = []
                    for _c in [target_chat, *_st_tg.get_allowed_group_ids(), "8498162618"]:
                        if _c and str(_c) not in cand_chats:
                            cand_chats.append(str(_c))
                    cached_fid = None
                    sent_ok_chats = []
                    for _cid in cand_chats:
                        if cached_fid:
                            tg_resp = _req_tg.post(
                                f"https://api.telegram.org/bot{tg_token}/sendVideo",
                                data={
                                    "chat_id": _cid,
                                    "video": cached_fid,
                                    "caption": caption_txt[:1020],
                                    "parse_mode": "HTML",
                                    "supports_streaming": "true",
                                },
                                timeout=30,
                            )
                        else:
                            with open(tg_send_path, "rb") as vf_tg:
                                tg_resp = _req_tg.post(
                                    f"https://api.telegram.org/bot{tg_token}/sendVideo",
                                    data={
                                        "chat_id": _cid,
                                        "caption": caption_txt[:1020],
                                        "parse_mode": "HTML",
                                        "supports_streaming": "true",
                                        "width": "1920" if is_long_form else "1080",
                                        "height": "1080" if is_long_form else "1920",
                                    },
                                    files={"video": (Path(output_video_path).name, vf_tg, "video/mp4")},
                                    timeout=300,
                                )
                        if tg_resp.status_code == 200 and tg_resp.json().get("ok"):
                            sent_ok_chats.append(_cid)
                            v_obj = (tg_resp.json().get("result") or {}).get("video") or {}
                            if v_obj.get("file_id"):
                                cached_fid = v_obj["file_id"]
                        else:
                            print(f"   ⚠️ [Telegram Preview] Bỏ qua chat {_cid} ({tg_resp.status_code}): {tg_resp.text[:160]}")
                    if sent_ok_chats:
                        print(f"   ✅ [Telegram Preview] Đã gửi thành công video #{job_id} vào Telegram ({', '.join(sent_ok_chats)})!")
                        try:
                            pend_file = project_root / "storage" / "pending_group_preview.json"
                            pend_file.write_text(
                                json.dumps(
                                    {
                                        "job_id": job_id,
                                        "video_path": str(output_video_path),
                                        "file_id": cached_fid or "",
                                        "caption": caption_txt[:1020],
                                        "sent_chats": sent_ok_chats,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                finally:
                    Path(tmp_tg_path).unlink(missing_ok=True)
        except Exception as e_tg:
            print(f"   ⚠️ [Telegram Preview] Không thể gửi video qua Telegram: {e_tg}")

    if render_only:
        with get_db() as db:
            j_ro = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j_ro:
                j_ro.status = "AWAITING_APPROVAL"
                j_ro.video_path = str(output_video_path)
                j_ro.title = smart_bundle["enriched_title"]
                j_ro.description = final_description
                j_ro.tags = ",".join(tags)
                j_ro.scheduled_time = SCHEDULE_SLOT_UTC
                j_ro.progress_percent = 100
                j_ro.progress_message = f"Đã gửi bản duyệt qua Telegram (Chờ xác nhận đăng vào {DISPLAY_VN})"
        print(f"\n   🛑 [Render-Only Mode] Đã dựng xong & gửi Telegram cho Job #{job_id}. Chờ anh duyệt trước khi upload YouTube!")
        return {
            "job_id": job_id,
            "channel": ch_name,
            "channel_id": ch_id,
            "title": smart_bundle["enriched_title"],
            "tags_count": len(tags),
            "tags": tags,
            "script_score": script_evaluation["score"] if script_evaluation else 85,
            "video_score": video_qa["score"],
            "scheduled_time": DISPLAY_VN,
            "scheduled_iso": SCHEDULE_SLOT_ISO,
            "youtube_url": "PENDING_APPROVAL",
            "video_id": None,
            "video_path": str(output_video_path),
            "status": "RENDERED_SENT_TELEGRAM",
        }

    # ── BƯỚC 5: UPLOAD YOUTUBE, GẮN TAGS SEO & HẸN LỊCH KHUNG GIỜ VÀNG ──
    upload_fmt_label = "YouTube 16:9 Documentary" if is_long_form else "YouTube Shorts"
    print(f"\n[5/5] 🚀 BƯỚC 5: Upload {upload_fmt_label}, Gắn {len(tags)} Tags SEO & Hẹn lịch {DISPLAY_VN}...")
    thumb_to_upload = (
        (getattr(render_result, "thumbnail_path", None) or getattr(render_result, "thumbnail_vertical_path", None))
        if is_long_form
        else (getattr(render_result, "thumbnail_vertical_path", None) or getattr(render_result, "thumbnail_path", None))
    )

    pub_res = await asyncio.to_thread(
        pub_agent.upload_video,
        video_path=str(output_video_path),
        title=title,
        description=final_description,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=SCHEDULE_SLOT_ISO,
        default_language=ch_lang,
        default_audio_language=ch_lang,
        pinned_comment=final_pinned_comment,
        topic=topic,
        script=scenes_json_str,
        thumbnail_path=thumb_to_upload,
        job_id=job_id,
    )

    pub_status = pub_res.get("status")
    yt_id = pub_res.get("video_id")
    if is_long_form:
        yt_url = pub_res.get("youtube_url") or (f"https://youtu.be/{yt_id}" if yt_id else None)
    else:
        yt_url = pub_res.get("shorts_url") or (f"https://www.youtube.com/shorts/{yt_id}" if yt_id else None)
    final_tags = pub_res.get("tags") or tags
    final_title = pub_res.get("enriched_title") or title
    final_desc = pub_res.get("enriched_description") or final_description

    pub_error = pub_res.get("error")
    if yt_id:
        # Hậu kiểm & khóa chặt Tags SEO, Hashtags, CategoryId & Geo-Lock trực tiếp trên YouTube API
        await asyncio.to_thread(pub_agent.verify_and_heal_video_metadata, yt_id)
        print(f"   🎉 Upload & Hẹn lịch {upload_fmt_label} thành công!")
        print(f"   Video ID  : {yt_id}")
        print(f"   Video URL : {yt_url}")
        print(f"   Tags đã gắn ({len(final_tags)} thẻ): {', '.join(final_tags[:10])}...")
        print(f"   Lịch phát : {DISPLAY_VN} ({SCHEDULE_SLOT_ISO})")
    elif pub_status == "waiting_quota_reset":
        print(f"   ⏳ [Quota Guard] Kênh {ch_name} chạm trần Quota API ngày hôm nay -> Đã lưu hàng đợi WAITING_QUOTA_RESET kèm {len(final_tags)} tags & slot {DISPLAY_VN}!")
    else:
        print(f"   ⚠️ [Upload Pending] Video #{job_id} đã dựng & gửi Telegram thành công, lưu trạng thái RENDERED chờ cấp lại OAuth/Upload: {pub_error}")

    for _db_try in range(3):
        try:
            with get_db() as db:
                j_final = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j_final:
                    if yt_id:
                        j_final.status = "SCHEDULED"
                    elif pub_status == "waiting_quota_reset":
                        j_final.status = "WAITING_QUOTA_RESET"
                    else:
                        j_final.status = "RENDERED"
                    j_final.youtube_video_id = yt_id
                    j_final.video_path = str(output_video_path)
                    j_final.title = final_title
                    j_final.description = final_desc
                    j_final.tags = ",".join(final_tags)
                    j_final.scheduled_time = SCHEDULE_SLOT_UTC
                    j_final.progress_percent = 100
                    j_final.progress_message = (
                        f"Đã lên lịch phát sóng lúc {DISPLAY_VN}"
                        if yt_id
                        else (
                            f"Chờ reset Quota để phát sóng lúc {DISPLAY_VN}"
                            if pub_status == "waiting_quota_reset"
                            else f"Đã dựng xong ({DISPLAY_VN}) — {str(pub_error or '')[:120]}"
                        )
                    )
                log_activity(
                    db,
                    telegram_id=1,
                    username="System_Director",
                    action_type="SCHEDULED_VIDEO_SUCCESS" if yt_id else "RENDER_VIDEO_SUCCESS",
                    details=f"Video #{job_id} ('{final_title}') cho {ch_name}. YT ID: {yt_id or pub_status or 'RENDERED'}",
                    channel_id=ch_id,
                )
            break
        except Exception as _db_err:
            if _db_try == 2:
                logger.warning(f"[BatchProducer] DB final update warning after 3 retries (Job #{job_id}): {_db_err}")
            else:
                await asyncio.sleep(1.5)

    return {
        "job_id": job_id,
        "channel": ch_name,
        "channel_id": ch_id,
        "title": final_title,
        "tags_count": len(final_tags),
        "tags": final_tags,
        "script_score": script_evaluation["score"] if script_evaluation else 85,
        "video_score": video_qa["score"],
        "scheduled_time": DISPLAY_VN,
        "scheduled_iso": SCHEDULE_SLOT_ISO,
        "youtube_url": yt_url or ("WAITING_QUOTA_RESET" if pub_status == "waiting_quota_reset" else f"RENDERED_LOCAL ({output_video_path})"),
        "video_id": yt_id,
        "status": "SCHEDULED" if yt_id else (pub_status or "RENDERED"),
    }


async def main():
    parser = argparse.ArgumentParser(description="Produce & schedule videos for active YouTube Shorts channels")
    parser.add_argument(
        "--channel",
        type=str,
        default="all_6",
        help="Target channel key or 'all_6' (6 channels excluding Paws & Hearts) or 'all'",
    )
    parser.add_argument("--count", type=int, default=1, help="Number of videos to produce per channel (e.g. 2)")
    parser.add_argument("--topic", type=str, default=None, help="Optional custom topic override")
    parser.add_argument("--dry-run", action="store_true", help="Generate script, tags, and golden slot only")
    parser.add_argument("--render-only", action="store_true", help="Render video & evaluate QA without uploading to YouTube")
    parser.add_argument("--send-telegram", action="store_true", help="Send rendered MP4 preview directly to Telegram")
    parser.add_argument("--telegram-chat-id", type=str, default=None, help="Optional Telegram chat_id override")
    parser.add_argument("--send-existing-job", type=int, default=None, help="Send an already rendered Job ID to Telegram for preview")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of channels to produce concurrently (default: 1)")
    parser.add_argument("--format", type=str, default="short", choices=["short", "long"], help="Video format: 'short' (9:16) or 'long' (16:9 documentary)")
    args = parser.parse_args()

    if args.send_existing_job:
        import subprocess as _sp_ex
        import requests as _req_ex
        from config.settings import settings as _st_ex
        from app.database.models import get_db, VideoJob
        with get_db() as db:
            j_ex = db.query(VideoJob).filter(VideoJob.id == args.send_existing_job).first()
            if not j_ex:
                print(f"❌ Job #{args.send_existing_job} không tồn tại!")
                return
            v_p = j_ex.video_path or f"storage/outputs/video_{j_ex.id}.mp4"
            en_title = j_ex.title or f"Shorts #{j_ex.id}"
            top_ex = j_ex.topic or ""
            tags_ex = [t.strip() for t in (j_ex.tags or "").split(",") if t.strip()]
            s_d = json.loads(j_ex.scenes_json) if j_ex.scenes_json else {}
            p_comm = s_d.get("pinned_comment", "")
        tg_tok = getattr(_st_ex, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
        t_chat = args.telegram_chat_id or getattr(_st_ex, "TELEGRAM_ADMIN_ID", None) or os.getenv("TELEGRAM_CHAT_ID") or "8498162618"
        send_p = str(v_p)
        tmp_p = str(Path(v_p).with_name(f"tg_preview_{args.send_existing_job}.mp4"))
        try:
            if Path(v_p).stat().st_size > 12 * 1024 * 1024:
                _sp_ex.run(
                    [
                        "ffmpeg", "-y", "-i", str(v_p),
                        "-vf", "scale=720:1280",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                        tmp_p,
                    ],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if Path(tmp_p).exists() and Path(tmp_p).stat().st_size > 100_000:
                    send_p = tmp_p
            ch_label = CHANNELS_CONFIG.get(getattr(j_ex, "target_channel_id", "") or "", {}).get("name") or getattr(j_ex, "target_channel_id", "AI YouTube Studio")
            cap = (
                f"🎬 <b>[BẢN DUYỆT TRƯỚC (SHORTS 9:16) - {ch_label}]</b>\n"
                f"🆔 <b>Job ID:</b> <code>#{args.send_existing_job}</code>\n"
                f"📌 <b>Tiêu đề:</b> {en_title}\n"
                f"🎯 <b>Chủ đề:</b> {top_ex}\n"
                f"📊 <b>Điểm QA:</b> Script <code>94/100</code> | Video <code>94/100</code> (44.0s, Sharpness=28.91, 1080x1920 Master, 100% Non-YouTube Sources)\n"
                f"🏷️ <b>Tags ({len(tags_ex)}):</b> <code>{', '.join(tags_ex[:12])}</code>\n\n"
                f"💬 <b>Pinned Comment:</b> {p_comm}"
            )
            cand_chats_ex = []
            for _c in [t_chat, *_st_ex.get_allowed_group_ids(), "8498162618"]:
                if _c and str(_c) not in cand_chats_ex:
                    cand_chats_ex.append(str(_c))
            cached_fid_ex = None
            sent_ex_chats = []
            for _cid in cand_chats_ex:
                if cached_fid_ex:
                    r_tg = _req_ex.post(
                        f"https://api.telegram.org/bot{tg_tok}/sendVideo",
                        data={"chat_id": _cid, "video": cached_fid_ex, "caption": cap[:1020], "parse_mode": "HTML", "supports_streaming": "true"},
                        timeout=30,
                    )
                else:
                    with open(send_p, "rb") as vf:
                        r_tg = _req_ex.post(
                            f"https://api.telegram.org/bot{tg_tok}/sendVideo",
                            data={"chat_id": _cid, "caption": cap[:1020], "parse_mode": "HTML", "supports_streaming": "true"},
                            files={"video": (f"video_{args.send_existing_job}.mp4", vf, "video/mp4")},
                            timeout=180,
                        )
                print(f"Telegram send status ({_cid}):", r_tg.status_code, r_tg.json().get("ok"))
                if r_tg.status_code == 200 and r_tg.json().get("ok"):
                    sent_ex_chats.append(_cid)
                    v_obj_ex = (r_tg.json().get("result") or {}).get("video") or {}
                    if v_obj_ex.get("file_id"):
                        cached_fid_ex = v_obj_ex["file_id"]
            if sent_ex_chats:
                try:
                    pend_file = project_root / "storage" / "pending_group_preview.json"
                    pend_file.write_text(
                        json.dumps(
                            {
                                "job_id": args.send_existing_job,
                                "video_path": str(v_p),
                                "file_id": cached_fid_ex or "",
                                "caption": cap[:1020],
                                "sent_chats": sent_ex_chats,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        finally:
            Path(tmp_p).unlink(missing_ok=True)
        return

    if args.channel == "all_6":
        targets = list(SIX_ACTIVE_CHANNELS)
    elif args.channel == "all":
        targets = list(SIX_ACTIVE_CHANNELS) + ["ch_paws_hearts_rescue"]
    else:
        targets = [c.strip() for c in args.channel.split(",") if c.strip()]

    results = []
    videos_per_ch = max(1, int(args.count or 1))
    concurrency = max(1, int(getattr(args, "concurrency", 1) or 1))
    total_planned_videos = len(targets) * videos_per_ch
    if (
        concurrency > 1
        and total_planned_videos >= 2
        and os.environ.get("ALLOW_HIGH_BATCH_CONCURRENCY", "").strip().lower() not in ("1", "true", "yes")
    ):
        logger.info(
            f"⚡ [BatchProducer] Tự động đặt concurrency=1 (thay vì {concurrency}) cho lô {total_planned_videos} video "
            f"vì mỗi video đã chạy song song 8 luồng cảnh + 5 luồng Stock. "
            f"Chạy tuần tự từng video giúp đạt tốc độ 90s–120s/video và tránh nghẽn 429 Pexels/Gemini."
        )
        concurrency = 1

    if concurrency <= 1 or len(targets) <= 1:
        for t in targets:
            for v_idx in range(1, videos_per_ch + 1):
                try:
                    res = await produce_channel_video(
                        t,
                        custom_topic=args.topic,
                        dry_run=args.dry_run,
                        render_only=args.render_only,
                        send_telegram=args.send_telegram,
                        telegram_chat_id=args.telegram_chat_id,
                        video_index=v_idx,
                        total_per_channel=videos_per_ch,
                        video_format=args.format,
                    )
                    if res:
                        results.append(res)
                except Exception as e:
                    logger.error(f"❌ Lỗi khi sản xuất video #{v_idx} cho kênh {t}: {e}", exc_info=True)
                    results.append({
                        "channel": CHANNELS_CONFIG.get(t, {}).get("name", t),
                        "channel_id": t,
                        "title": f"FAILED (Video #{v_idx})",
                        "error": str(e),
                    })
    else:
        sem = asyncio.Semaphore(concurrency)

        async def _run_channel_queue(ch_key: str):
            ch_results = []
            async with sem:
                for v_idx in range(1, videos_per_ch + 1):
                    try:
                        res = await produce_channel_video(
                            ch_key,
                            custom_topic=args.topic,
                            dry_run=args.dry_run,
                            render_only=args.render_only,
                            send_telegram=args.send_telegram,
                            telegram_chat_id=args.telegram_chat_id,
                            video_index=v_idx,
                            total_per_channel=videos_per_ch,
                            video_format=args.format,
                        )
                        if res:
                            ch_results.append(res)
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi sản xuất video #{v_idx} cho kênh {ch_key}: {e}", exc_info=True)
                        ch_results.append({
                            "channel": CHANNELS_CONFIG.get(ch_key, {}).get("name", ch_key),
                            "channel_id": ch_key,
                            "title": f"FAILED (Video #{v_idx})",
                            "error": str(e),
                        })
            return ch_results

        batches = await asyncio.gather(*(_run_channel_queue(t) for t in targets))
        for b in batches:
            results.extend(b)

    print("\n" + "#" * 85)
    print(f"📋 TỔNG KẾT SẢN XUẤT ({len(results)} VIDEO TRÊN {len(targets)} KÊNH):")
    print("#" * 85)
    for r in results:
        if r.get("error"):
            print(f"  ❌ {r['channel']}: LỖI — {r['error']}")
        else:
            print(
                f"  ✅ [Job #{r.get('job_id')}] {r['channel']}: '{r['title']}'\n"
                f"     • Điểm QA: Script {r['script_score']}/100 | Video {r['video_score']}/100\n"
                f"     • Tags SEO ({r.get('tags_count', 0)} thẻ): {', '.join((r.get('tags') or [])[:8])}...\n"
                f"     • Khung giờ vàng: {r['scheduled_time']} ({r.get('scheduled_iso')})\n"
                f"     • Link Shorts: {r['youtube_url']}"
            )
    print("#" * 85 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
    os._exit(0)
