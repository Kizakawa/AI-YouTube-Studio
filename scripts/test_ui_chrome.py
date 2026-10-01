import sys, asyncio
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import json
import os
import subprocess
import time
import urllib.request
import websockets
import base64

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
USER_DATA_DIR = r"C:\Users\ADMIN\AppData\Local\Temp\chrome_test_profile_" + str(int(time.time()))

async def send_cmd(ws, msg_id, method, params=None, timeout=10):
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            data = json.loads(resp)
            if data.get("id") == msg_id:
                return data
        except asyncio.TimeoutError:
            continue
    raise TimeoutError(f"Timeout waiting for response to {method} (id={msg_id})")

async def run_test():
    print(f"Launching headless Chrome on port {PORT}...", flush=True)
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={PORT}",
        "--headless=new",
        f"--user-data-dir={USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1280,900",
        "about:blank"
    ]
    proc = subprocess.Popen(cmd)
    
    try:
        # Wait for debugging port
        tabs = []
        for _ in range(30):
            time.sleep(0.5)
            try:
                tabs_raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read()
                all_tabs = json.loads(tabs_raw)
                tabs = [t for t in all_tabs if t.get("type") == "page"]
                if tabs:
                    break
            except Exception:
                pass
        else:
            raise RuntimeError("Could not find a 'page' tab in Chrome")

        target_ws_url = tabs[0]["webSocketDebuggerUrl"]
        print(f"Connected to WS: {target_ws_url}", flush=True)

        async with websockets.connect(target_ws_url) as ws:
            # Enable domains
            await send_cmd(ws, 1, "Page.enable")
            await send_cmd(ws, 2, "Runtime.enable")
            await send_cmd(ws, 3, "DOM.enable")

            # Navigate to http://localhost:8000/
            print("Navigating to http://localhost:8000/ ...", flush=True)
            await send_cmd(ws, 4, "Page.navigate", {"url": "http://localhost:8000/"})
            await asyncio.sleep(2.5)

            # Switch to 'repurpose' tab and load clips
            print("Switching tab to 'repurpose' and loading clips...", flush=True)
            await send_cmd(ws, 5, "Runtime.evaluate", {
                "expression": """
                    (async () => {
                        switchTab('repurpose');
                        await loadRepurposerClips();
                        return { clipsCount: allRepurposedClips ? allRepurposedClips.length : 0 };
                    })()
                """,
                "awaitPromise": True,
                "returnByValue": True
            })
            await asyncio.sleep(1.5)

            # Trigger openRepurposedPlayer via click
            print("Triggering click on '▶ Xem' button...", flush=True)
            click_res = await send_cmd(ws, 6, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const playBtn = document.querySelector('.repurposed-card-play-btn');
                        if (playBtn) {
                            playBtn.click();
                            return 'Clicked .repurposed-card-play-btn';
                        }
                        if (typeof allRepurposedClips !== 'undefined' && allRepurposedClips.length > 0) {
                            const c = allRepurposedClips[0];
                            openRepurposedPlayer(c.url, c.title || c.filename, c.filename);
                            return 'Direct call openRepurposedPlayer with clip ' + c.filename;
                        }
                        return 'ERROR: No clips or playBtn found';
                    })()
                """,
                "returnByValue": True
            })
            print("Click result:", click_res.get("result", {}).get("result", {}).get("value"), flush=True)
            # Wait for requestAnimationFrame and video load
            await asyncio.sleep(2.0)

            # Inspect modal & video element in DOM
            res = await send_cmd(ws, 7, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const modal = document.getElementById('modal-repurposed-player');
                        const vidEl = document.getElementById('repurposed-video-element');
                        if (!modal || !vidEl) return { error: 'elements not found' };
                        
                        const mStyle = window.getComputedStyle(modal);
                        const vStyle = window.getComputedStyle(vidEl);
                        const mRect = modal.getBoundingClientRect();
                        const vRect = vidEl.getBoundingClientRect();
                        const parentModal = modal.closest('#modal-publish-yt');

                        return {
                            modal: {
                                display: mStyle.display,
                                visibility: mStyle.visibility,
                                opacity: mStyle.opacity,
                                zIndex: mStyle.zIndex,
                                rect: { x: Math.round(mRect.x), y: Math.round(mRect.y), width: Math.round(mRect.width), height: Math.round(mRect.height) },
                                isVisibleToUser: mStyle.display === 'flex' && mRect.width > 0 && mRect.height > 0
                            },
                            video: {
                                src: vidEl.src,
                                display: vStyle.display,
                                visibility: vStyle.visibility,
                                rect: { x: Math.round(vRect.x), y: Math.round(vRect.y), width: Math.round(vRect.width), height: Math.round(vRect.height) },
                                isVisibleToUser: vStyle.display !== 'none' && vRect.width > 0 && vRect.height > 0,
                                paused: vidEl.paused,
                                currentTime: vidEl.currentTime,
                                duration: vidEl.duration,
                                readyState: vidEl.readyState
                            },
                            nestedInsidePublishYt: parentModal !== null
                        };
                    })()
                """,
                "returnByValue": True
            })
            eval_res = res.get("result", {}).get("result", {}).get("value")
            print("\n================ EVALUATION RESULT ================", flush=True)
            print(json.dumps(eval_res, indent=2, ensure_ascii=False), flush=True)
            print("====================================================\n", flush=True)

            # Capture screenshot
            print("Capturing screenshot...", flush=True)
            shot_res = await send_cmd(ws, 8, "Page.captureScreenshot", {"format": "png"})
            img_b64 = shot_res.get("result", {}).get("data", "")
            if img_b64:
                out_path = r"d:\AI_YouTube_Project_Ready\AI-youtube-main\scripts\repurposed_player_verified.png"
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(img_b64))
                print(f"Screenshot successfully saved to: {out_path}", flush=True)

    finally:
        proc.kill()
        print("Chrome closed.", flush=True)

if __name__ == "__main__":
    asyncio.run(run_test())
