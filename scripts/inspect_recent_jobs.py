import json
import sys
sys.stdout.reconfigure(encoding='utf-8')
from app.database.models import get_db, VideoJob

with get_db() as db:
    jobs = db.query(VideoJob).order_by(VideoJob.id.desc()).limit(3).all()
    for job in jobs:
        print(f"\n==================== JOB #{job.id} ====================")
        print(f"Status: {job.status} | Channel: {job.target_channel_id}")
        print(f"Title: {job.title}")
        print(f"Topic: {job.topic}")
        if job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                scenes = s_data.get('scenes', [])
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
