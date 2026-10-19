"""
app.services.entity_tracker — Hệ Thống Giám Sát & Chống Trùng Lặp Thực Thể (Entity Memory & Cooldown Tracker).

Nhiệm vụ:
1. Duy trì cửa sổ trượt (Sliding Window) gồm N kịch bản gần nhất (mặc định 15) của từng kênh.
2. Trích xuất các thực thể chính (Canonical Entities: tên nhân vật, địa danh, hiện tượng, cổ vật, sự kiện lịch sử).
3. Cung cấp danh sách cooldown_entities để tiêm vào prompt sinh kịch bản và cung cấp cho ScriptValidationLinter.
4. Ngăn chặn hiện tượng lặp lại cùng một dữ kiện (như 'The Bloop', 'Mariana Trench') trong các video liên tiếp.
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional, Set
from datetime import datetime

from app.database.models import get_db, VideoJob

logger = logging.getLogger(__name__)

# Cache bộ nhớ tạm thời cho cooldown entities
_entity_cache: Dict[str, Dict[str, Any]] = {}


def extract_key_entities_heuristic(title: str, script_text: str, primary_entity: Optional[str] = None) -> List[str]:
    """
    Trích xuất thực thể theo heuristic nhận diện từ viết hoa (Proper Nouns) và các cụm danh từ riêng.
    """
    entities: Set[str] = set()

    if primary_entity and len(primary_entity.strip()) > 2:
        entities.add(primary_entity.strip().lower())

    text = f"{title}. {script_text}"

    # 1. Tìm các cụm từ viết hoa 2-4 từ (Proper Noun Phrases)
    # Ví dụ: "Mariana Trench", "The Bloop", "Point Nemo", "Baltic Sea Anomaly", "Kola Borehole"
    proper_nouns = re.findall(r'\b(?:The\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b', text)
    for pn in proper_nouns:
        clean_pn = pn.strip().lower()
        # Bỏ qua các cụm từ chung chung
        if clean_pn in {
            "youtube shorts", "part 2", "subscribe for", "drop your",
            "united states", "don't forget", "follow for"
        }:
            continue
        if len(clean_pn) > 3:
            entities.add(clean_pn)

    # 2. Tìm các thực thể có năm đi kèm (Ví dụ: "1997 Navy Sonar", "1970 Apollo")
    year_entities = re.findall(r'\b(?:19|20)\d{2}\s+[A-Za-z\s]{3,20}\b', text)
    for ye in year_entities:
        clean_ye = ye.strip().lower()
        if len(clean_ye) > 4:
            entities.add(clean_ye)

    return sorted(list(entities))[:6]


def get_channel_cooldown_entities(channel_id: str, limit_jobs: int = 15) -> List[str]:
    """
    Lấy danh sách các thực thể đang trong thời gian cooldown từ N video gần nhất của kênh.
    """
    if not channel_id:
        return []

    cooldown_entities: Set[str] = set()

    try:
        with get_db() as db:
            # Chỉ lấy các video ĐÃ ĐĂNG LÊN YOUTUBE (có youtube_video_id) để đưa thực thể vào bộ nhớ cooldown
            recent_jobs = (
                db.query(VideoJob)
                .filter(VideoJob.target_channel_id == channel_id)
                .filter(VideoJob.youtube_video_id.isnot(None))
                .filter(VideoJob.youtube_video_id != "")
                .order_by(VideoJob.id.desc())
                .limit(limit_jobs)
                .all()
            )

            for job in recent_jobs:
                # 1. Nếu có scenes_json, đọc primary_entity
                if job.scenes_json:
                    try:
                        s_data = json.loads(job.scenes_json)
                        if isinstance(s_data, dict) and s_data.get("primary_entity"):
                            cooldown_entities.add(s_data["primary_entity"].strip().lower())
                    except Exception:
                        pass

                # 2. Trích xuất từ title và topic
                extracted = extract_key_entities_heuristic(
                    title=job.title or "",
                    script_text=job.script or "",
                    primary_entity=None
                )
                for ent in extracted:
                    cooldown_entities.add(ent)

    except Exception as e:
        logger.warning(f"[EntityTracker] Lỗi truy vấn cooldown entities cho kênh {channel_id}: {e}")

    logger.info(
        f"[EntityTracker] Kênh '{channel_id}': Tìm thấy {len(cooldown_entities)} thực thể cooldown trong {limit_jobs} video gần nhất: "
        f"{list(cooldown_entities)[:5]}"
    )
    return sorted(list(cooldown_entities))


def register_job_entities(channel_id: str, script_contract, job_id: Optional[int] = None) -> List[str]:
    """
    Đăng ký và ghi nhận các thực thể của kịch bản vừa được tạo vào bộ nhớ.
    """
    entities = extract_key_entities_heuristic(
        title=script_contract.title,
        script_text=script_contract.full_narration,
        primary_entity=script_contract.primary_entity
    )
    logger.info(f"[EntityTracker] Đã đăng ký {len(entities)} thực thể mới cho Job #{job_id}: {entities}")
    return entities
