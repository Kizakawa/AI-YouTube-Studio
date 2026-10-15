# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import subprocess
from datetime import datetime

# Dam bao encoding an toan tuyet doi tren Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TRANSCRIPT_PATH = r"C:\Users\vv1nc\.gemini\antigravity\brain\1b893567-c15f-4890-af5e-d8bfc3f62229\.system_generated\logs\transcript.jsonl"
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_PATH = os.path.join(BASE_DIR, "storage", "watchdog_shutdown.log")
QUIET_SECONDS = 120  # Doi 2 phut khong co hoat dong sau phan hoi cuoi cung


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    if sys.stdout is not None:
        try:
            print(formatted, flush=True)
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def is_ffmpeg_running() -> bool:
    try:
        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe"], capture_output=True, text=True)
        return "ffmpeg.exe" in res.stdout
    except Exception:
        return False


last_reported_step = None
last_heartbeat_time = 0


def check_task_completed() -> bool:
    global last_reported_step, last_heartbeat_time

    if not os.path.exists(TRANSCRIPT_PATH):
        log(f"[CANH BAO] Khong tim thay transcript: {TRANSCRIPT_PATH}")
        return False

    mtime = os.path.getmtime(TRANSCRIPT_PATH)
    now = time.time()
    time_diff = now - mtime

    try:
        with open(TRANSCRIPT_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if not lines:
            return False
        last_obj = json.loads(lines[-1].strip())
    except Exception as e:
        log(f"Loi khi doc transcript: {e}")
        return False

    step_index = last_obj.get("step_index")
    obj_type = last_obj.get("type")
    tool_calls = last_obj.get("tool_calls")

    # Khi agent hoan tat mot luot tra loi (PLANNER_RESPONSE va khong con tool_calls)
    is_turn_end = (obj_type == "PLANNER_RESPONSE" and not tool_calls)

    if is_turn_end:
        log(f"[XAC NHAN] Agent da hoan tat phan hoi tai Step {step_index}! Dem nguoc yen lang: {int(time_diff)}s / {QUIET_SECONDS}s")
        if time_diff >= QUIET_SECONDS:
            if is_ffmpeg_running():
                log("[DANG CHO] Phan hoi da xong nhung ffmpeg.exe van dang render video. Tiep tuc doi...")
                return False
            return True
    else:
        if step_index != last_reported_step or (now - last_heartbeat_time >= 60):
            last_reported_step = step_index
            last_heartbeat_time = now
            log(f"[TIEN TRINH] Step {step_index} ({obj_type}). Hoat dong cuoi: {int(time_diff)}s truoc.")

    return False


def trigger_shutdown():
    log("=================================================================")
    log(">>> TASK ANTIGRAVITY DA HOAN TAT! LAPTOP SE TAT TRONG 60 GIAY <<<")
    log("=================================================================")

    shutdown_cmd = [
        "shutdown.exe",
        "/s",
        "/f",
        "/t", "60",
        "/c", "Antigravity task da hoan tat! May se tat sau 60 giay. Go 'shutdown /a' de huy."
    ]
    try:
        subprocess.run(shutdown_cmd, check=True)
        log("Da phat lenh thanh cong: shutdown /s /f /t 60")
        log("Co the huy bang script cancel_shutdown.bat hoac go 'shutdown /a'")
    except Exception as e:
        log(f"Loi khi thuc thi shutdown.exe: {e}")


def main():
    log("=== [START] Khoi dong Antigravity Watchdog Auto-Shutdown ===")
    log(f"Theo doi: {TRANSCRIPT_PATH}")
    log(f"File log: {LOG_PATH}")
    log(f"Thoi gian cho yen lang: {QUIET_SECONDS}s")

    while True:
        try:
            if check_task_completed():
                trigger_shutdown()
                break
        except Exception as e:
            log(f"Loi watchdog loop: {e}")

        time.sleep(15)


if __name__ == "__main__":
    main()
