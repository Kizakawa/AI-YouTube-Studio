import sys
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.models import get_db, VideoJob, QuotaTracker

with get_db() as db:
    for jid in [311, 312]:
        j = db.query(VideoJob).filter(VideoJob.id == jid).first()
        if j:
            d = json.loads(j.scenes_json) if j.scenes_json else {}
            print(f"=== JOB #{j.id} ===")
            print(f"Channel: {j.target_channel_id}")
            print(f"Title: {j.title}")
            print(f"Video path: {j.video_path}")
            print(f"Scheduled time: {j.scheduled_time}")
            print(f"Pinned comment: {d.get('pinned_comment')}")
            print(f"Description len: {len(j.description or '')}")
            print(f"Tags: {j.tags}")

    print("\n=== QUOTA TRACKERS ===")
    for q in db.query(QuotaTracker).filter(QuotaTracker.cycle_date == '2026-10-12').all():
        print(f"project_id={q.project_id}, cycle_date={q.cycle_date}, used_units={q.used_units}, limit={q.max_daily_quota}")
