#!/usr/bin/env python3
"""
Unified YouTube Channel Management & Multi-Platform Viral Intelligence CLI
Powered by Panniantong/Agent-Reach + YouTube Data API v3 & YouTube Analytics API v2.
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = WORKSPACE_ROOT / "AI-youtube-main"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

AGENT_REACH_BIN = PROJECT_ROOT / "venv" / "Scripts" / "agent-reach.exe"

NICHE_SEARCH_PRESETS: Dict[str, Dict[str, Any]] = {
    "01_animal_rescue_paws": {
        "channels": ["ch_paws_hearts_rescue"],
        "youtube_queries": [
            "stray dog rescue transformation shorts",
            "abandoned puppy 48 hour recovery miracle shorts",
        ],
        "reddit_queries": [
            "site:reddit.com/r/BeforeNAfterAdoption stray dog rescue",
            "site:reddit.com/r/rescuedogs transformation story",
        ],
    },
    "02_wildlife_predators": {
        "channels": ["ch_wild_odyssey", "ch_odisseia_selvagem"],
        "youtube_queries": [
            "predator vs prey bite force PSI wildlife shorts",
            "honey badger vs black mamba reflex wildlife shorts",
        ],
        "reddit_queries": [
            "site:reddit.com/r/natureismetal predator showdown",
            "site:reddit.com/r/HardcoreNature animal survival",
        ],
    },
    "03_deep_sea_mysteries": {
        "channels": ["ucsrjiil67qxidtxt70z-iwa", "ch_misterios_do_mundo"],
        "youtube_queries": [
            "deep sea creature vs extreme ocean pressure shorts",
            "deadliest natural phenomenon on earth shorts",
        ],
        "reddit_queries": [
            "site:reddit.com/r/TheDepthsBelow terrifying deep sea",
            "site:reddit.com/r/NatureIsFuckingLit extreme phenomenon",
        ],
    },
    "04_dark_psychology": {
        "channels": ["channel_maya"],
        "youtube_queries": [
            "dark psychology experiment human behavior shorts",
            "body language trick fbi interrogator shorts",
        ],
        "reddit_queries": [
            "site:reddit.com/r/psychology fascinating psychological effect",
            "site:reddit.com/r/socialengineering subtle psychological trick",
        ],
    },
    "05_alternate_history": {
        "channels": ["@alternatehistorywhatif-e2x"],
        "youtube_queries": [
            "what if history biggest disaster eruption shorts",
            "untold historical near miss catastrophe shorts",
        ],
        "reddit_queries": [
            "site:reddit.com/r/HistoryWhatIf most dramatic turning point",
            "site:reddit.com/r/AskHistorians catastrophic event",
        ],
    },
}


def run_agent_reach(args: List[str], timeout: int = 45) -> Dict[str, Any]:
    """Execute agent-reach CLI and return parsed JSON output."""
    cmd = [str(AGENT_REACH_BIN)] + args
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout = (proc.stdout or "").strip()
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        return {"ok": False, "raw_stdout": stdout, "raw_stderr": proc.stderr}
    except Exception as exc:
        return {"ok": False, "error": {"code": "EXEC_ERROR", "message": str(exc)}}


def run_live_7channel_audit(days_back: int = 10) -> Dict[str, Any]:
    """Fetch 100% real metrics from YouTube Data API v3 + YouTube Analytics API v2 across all 7 channels."""
    import yaml
    from app.utils.security import load_encrypted_json
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    cfg_path = PROJECT_ROOT / "config" / "channels.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    end_date = datetime.date.today().isoformat()
    start_date = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    month_start = (datetime.date.today() - datetime.timedelta(days=28)).isoformat()

    report_channels: List[Dict[str, Any]] = []
    for ch in cfg.get("channels", []):
        cid = ch["id"]
        tok_p = PROJECT_ROOT / "credentials" / "channels" / cid / "youtube_token.json"
        if not tok_p.exists():
            report_channels.append({"id": cid, "name": ch.get("name"), "status": "MISSING_TOKEN"})
            continue

        try:
            td = load_encrypted_json(tok_p)
            creds = Credentials.from_authorized_user_info(td)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

            yt = build("youtube", "v3", credentials=creds)
            yta = build("youtubeAnalytics", "v2", credentials=creds)

            c_res = yt.channels().list(mine=True, part="snippet,statistics,contentDetails").execute()
            item = (c_res.get("items") or [{}])[0]
            stats = item.get("statistics", {})
            uploads_pid = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

            p_res = (
                yt.playlistItems().list(playlistId=uploads_pid, part="contentDetails", maxResults=15).execute()
                if uploads_pid
                else {}
            )
            vids = [x["contentDetails"]["videoId"] for x in p_res.get("items", [])]
            v_res = yt.videos().list(id=",".join(vids), part="snippet,statistics,status").execute() if vids else {}

            analytics_map: Dict[str, Dict[str, float]] = {}
            ch_overview: Dict[str, Any] = {}
            try:
                ov = (
                    yta.reports()
                    .query(
                        ids="channel==MINE",
                        startDate=month_start,
                        endDate=end_date,
                        metrics="views,engagedViews,averageViewDuration,averageViewPercentage",
                    )
                    .execute()
                )
                if ov.get("rows"):
                    r0 = ov["rows"][0]
                    v_tot, eng_tot, avd, apv = r0[0], r0[1], r0[2], r0[3]
                    stayed = round((eng_tot / v_tot * 100.0), 1) if v_tot > 0 else 0.0
                    ch_overview = {
                        "views_28d": v_tot,
                        "engaged_views_28d": eng_tot,
                        "avd_sec": round(float(avd), 1),
                        "apv_pct": round(float(apv), 1),
                        "stayed_pct": stayed,
                        "swipe_away_pct": round(100.0 - stayed, 1),
                    }

                vrows = (
                    yta.reports()
                    .query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        dimensions="video",
                        metrics="views,engagedViews,averageViewDuration,averageViewPercentage",
                        sort="-views",
                        maxResults=25,
                    )
                    .execute()
                )
                for row in vrows.get("rows", []):
                    vid_id, v_cnt, eng_cnt, v_avd, v_apv = row[0], row[1], row[2], row[3], row[4]
                    v_stayed = round((eng_cnt / v_cnt * 100.0), 1) if v_cnt > 0 else 0.0
                    analytics_map[vid_id] = {
                        "analytics_views": v_cnt,
                        "engaged_views": eng_cnt,
                        "avd_sec": round(float(v_avd), 1),
                        "apv_pct": round(float(v_apv), 1),
                        "stayed_pct": v_stayed,
                        "swipe_away_pct": round(100.0 - v_stayed, 1),
                    }
            except Exception as yta_err:
                ch_overview["analytics_error"] = str(yta_err)[:120]

            recent_videos: List[Dict[str, Any]] = []
            for v in v_res.get("items", []):
                vid_id = v["id"]
                st = v.get("status", {})
                vst = v.get("statistics", {})
                sn = v.get("snippet", {})
                recent_videos.append(
                    {
                        "id": vid_id,
                        "title": sn.get("title", ""),
                        "publishedAt": sn.get("publishedAt"),
                        "privacyStatus": st.get("privacyStatus"),
                        "publishAt": st.get("publishAt"),
                        "views": int(vst.get("viewCount", 0)),
                        "likes": int(vst.get("likeCount", 0)),
                        "comments": int(vst.get("commentCount", 0)),
                        "retention": analytics_map.get(vid_id, "PENDING_24_48H"),
                    }
                )

            report_channels.append(
                {
                    "id": cid,
                    "name": ch.get("name"),
                    "subscribers": int(stats.get("subscriberCount", 0)),
                    "total_views": int(stats.get("viewCount", 0)),
                    "video_count": int(stats.get("videoCount", 0)),
                    "overview_28d": ch_overview,
                    "recent_videos": recent_videos,
                }
            )
        except Exception as err:
            report_channels.append({"id": cid, "name": ch.get("name"), "error": str(err)[:160]})

    return {
        "ok": True,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "channels": report_channels,
    }


def run_niche_radar(niche_key: str, limit: int = 3) -> Dict[str, Any]:
    """Scan YouTube & Reddit via Agent-Reach for viral Shorts and topics in a given niche."""
    preset = NICHE_SEARCH_PRESETS.get(niche_key)
    if not preset:
        for k, v in NICHE_SEARCH_PRESETS.items():
            if niche_key.lower() in k.lower() or any(niche_key.lower() in c.lower() for c in v["channels"]):
                preset = v
                niche_key = k
                break
    if not preset:
        return {"ok": False, "error": f"Unknown niche '{niche_key}'. Available: {list(NICHE_SEARCH_PRESETS.keys())}"}

    yt_results = []
    for q in preset["youtube_queries"]:
        res = run_agent_reach(["youtube", "search", q, "--limit", str(limit)])
        yt_results.append({"query": q, "items": res.get("items", [])})

    return {
        "ok": True,
        "niche": niche_key,
        "target_channels": preset["channels"],
        "youtube_viral_candidates": yt_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-Reach + YouTube 7-Channel Intelligence CLI")
    parser.add_argument("--mode", choices=["audit", "radar", "subtitle", "doctor"], default="audit")
    parser.add_argument("--niche", default="02_wildlife_predators", help="Niche key or channel ID for radar mode")
    parser.add_argument("--url", default="", help="YouTube video URL or ID for subtitle extraction")
    parser.add_argument("--lang", default="en,pt,vi", help="Preferred subtitle languages")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    if args.mode == "doctor":
        print(json.dumps(run_agent_reach(["doctor"]), ensure_ascii=False, indent=2))
    elif args.mode == "audit":
        print(json.dumps(run_live_7channel_audit(), ensure_ascii=False, indent=2))
    elif args.mode == "radar":
        print(json.dumps(run_niche_radar(args.niche, limit=args.limit), ensure_ascii=False, indent=2))
    elif args.mode == "subtitle":
        if not args.url:
            print(json.dumps({"ok": False, "error": "Missing --url parameter"}))
            sys.exit(1)
        print(json.dumps(run_agent_reach(["youtube", "subtitle", args.url, "--lang", args.lang]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
