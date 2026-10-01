import os
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
from app.database.models import get_db, VideoJob

with get_db() as db:
    jobs = db.query(VideoJob).order_by(VideoJob.id.desc()).limit(5).all()
    for job in jobs:
        print(f"\n==================== JOB #{job.id} ====================")
        print(f"Status: {job.status} | Progress: {job.progress_percent}% | Message: {job.progress_message}")
        print(f"Error: {job.error_message}")
        print(f"Channel: {job.target_channel_id} | Title: {job.title}")
        print(f"Topic: {job.topic}")
        if job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                scenes = s_data if isinstance(s_data, list) else s_data.get('scenes', [])
                print(f"Total scenes: {len(scenes)}")
                for sc in scenes:
                    sid = sc.get('scene_id')
                    m_path = sc.get('media_path')
                    m_type = sc.get('media_type')
                    sq = sc.get('stock_query')
                    narr = sc.get('narration', '')
                    print(f"  Scene {sid}: type={m_type} | sq={sq} | media={m_path}")
                    print(f"    Narration: {narr}")
            except Exception as e:
                print(f"  Error parsing scenes_json: {e}")
