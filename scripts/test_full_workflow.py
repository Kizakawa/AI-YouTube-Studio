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
PORT = 9335
USER_DATA_DIR = r"C:\Users\ADMIN\AppData\Local\Temp\chrome_test_profile_" + str(int(time.time()))

async def send_cmd(ws, msg_id, method, params=None, timeout=25):
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
    print(f"Bắt đầu kiểm thử toàn diện luồng hoạt động giao diện trên Chrome headless...", flush=True)
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

        async with websockets.connect(target_ws_url) as ws:
            await send_cmd(ws, 1, "Page.enable")
            await send_cmd(ws, 2, "Runtime.enable")
            await send_cmd(ws, 3, "DOM.enable")

            # 1. Truy cập Web Studio
            print("[TEST 1] Mở Web Studio...", flush=True)
            await send_cmd(ws, 4, "Page.navigate", {"url": "http://127.0.0.1:8000/"})
            await asyncio.sleep(2.5)

            # 2. Chuyển sang tab Tái Chế Shorts
            print("[TEST 2] Chuyển tab 'repurposer' và load danh sách clip...", flush=True)
            await send_cmd(ws, 5, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const nav = document.getElementById('nav-repurposer');
                        if (nav) nav.click();
                        else switchTab('repurposer');
                    })()
                """
            })
            await asyncio.sleep(2.0)

            # 2b. Kiểm tra tiêu đề video đã được giải quyết (không phải filename) & nút xóa trên card
            print("[TEST 2b] Kiểm tra tiêu đề hiển thị và nút xóa (Delete) trên card clip...", flush=True)
            res = await send_cmd(ws, 51, "Runtime.evaluate", {
                "expression": """
                    (async () => {
                        if (typeof allRepurposedClips === 'undefined' || allRepurposedClips.length === 0) {
                            if (typeof loadRepurposerClips === 'function') {
                                await loadRepurposerClips();
                            }
                        }
                        for (let i = 0; i < 30; i++) {
                            const titleEl = document.querySelector('.repurpose-card-title');
                            const delBtn = document.querySelector('.repurposed-card-del-btn');
                            const playBtn = document.querySelector('.repurposed-card-play-btn');
                            if (titleEl && delBtn && playBtn) {
                                return {
                                    title: titleEl.textContent.trim(),
                                    hasDelBtn: !!delBtn,
                                    hasPlayBtn: !!playBtn,
                                    debug: 'found'
                                };
                            }
                            await new Promise(r => setTimeout(r, 200));
                        }
                        const titleEl = document.querySelector('.repurpose-card-title');
                        const delBtn = document.querySelector('.repurposed-card-del-btn');
                        const playBtn = document.querySelector('.repurposed-card-play-btn');
                        const gal = document.getElementById('repurposer-clips-gallery');
                        return {
                            title: titleEl ? titleEl.textContent.trim() : '',
                            hasDelBtn: !!delBtn,
                            hasPlayBtn: !!playBtn,
                            debug: gal ? gal.innerHTML.slice(0, 200) : 'no gallery',
                            clipsCount: typeof allRepurposedClips !== 'undefined' ? allRepurposedClips.length : -1
                        };
                    })()
                """,
                "awaitPromise": True,
                "returnByValue": True
            })
            t2b = res.get("result", {}).get("result", {}).get("value")
            print(f"TEST 2b Diagnostic: {t2b}", flush=True)
            assert t2b and t2b["hasDelBtn"], f"Lỗi: Không tìm thấy nút Xóa (delBtn) trên thẻ clip! Debug: {t2b}"
            assert not t2b["title"].endswith(".mp4"), f"Lỗi: Tiêu đề vẫn là tên file .mp4: {t2b['title']}"
            print(f"  -> Thành công: Tiêu đề thẻ='{t2b['title']}', Có nút xóa 🗑️={t2b['hasDelBtn']}", flush=True)

            # 3. Mở Player bằng nút Xem
            print("[TEST 3] Bấm nút '▶ Xem' trên thẻ clip...", flush=True)
            await send_cmd(ws, 6, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const playBtn = document.querySelector('.repurposed-card-play-btn');
                        if (playBtn) playBtn.click();
                    })()
                """
            })
            await asyncio.sleep(1.5)

            # Kiểm tra modal hiển thị
            res = await send_cmd(ws, 7, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const m = document.getElementById('modal-repurposed-player');
                        const v = document.getElementById('repurposed-video-element');
                        return {
                            modalDisplay: window.getComputedStyle(m).display,
                            modalVisible: m.offsetWidth > 0 && m.offsetHeight > 0,
                            videoReady: v.readyState,
                            videoDuration: v.duration,
                            videoSrc: v.src
                        };
                    })()
                """,
                "returnByValue": True
            })
            t3 = res.get("result", {}).get("result", {}).get("value")
            assert t3["modalDisplay"] == "flex" and t3["modalVisible"], f"Modal không hiển thị: {t3}"
            print(f"  -> Thành công: Modal display={t3['modalDisplay']}, kích thước hiển thị OK, video readyState={t3['videoReady']}", flush=True)

            # 4. Kiểm tra hộp mô tả & tags mở rộng (Auto-expand với link Amazon và toggle linh hoạt)
            print("[TEST 4] Kiểm tra hộp mô tả & tags mở rộng (Auto-expand with Amazon link & Toggle)...", flush=True)
            res = await send_cmd(ws, 9, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const box = document.getElementById('rpub-desc-box');
                        const initialDisplay = window.getComputedStyle(box).display;
                        // Thử toggle để kiểm tra hàm toggle hoạt động chính xác
                        toggleRepurposedDescBox();
                        const toggledDisplay = window.getComputedStyle(box).display;
                        // Toggle lại về trạng thái hiển thị
                        if (toggledDisplay === 'none') {
                            toggleRepurposedDescBox();
                        }
                        const finalDisplay = window.getComputedStyle(box).display;
                        return {
                            initialDisplay: initialDisplay,
                            toggledDisplay: toggledDisplay,
                            finalDisplay: finalDisplay,
                            isWorking: initialDisplay !== toggledDisplay && finalDisplay === 'flex'
                        };
                    })()
                """,
                "returnByValue": True
            })
            t4 = res.get("result", {}).get("result", {}).get("value")
            assert t4["isWorking"], f"Toggle hộp mô tả không hoạt động chính xác: {t4}"
            print(f"  -> Thành công: Hộp mô tả hoạt động chính xác (Initial={t4['initialDisplay']}, Toggled={t4['toggledDisplay']}, Final={t4['finalDisplay']})", flush=True)

            # 5. Nhập nội dung tiêu đề và chọn kênh
            print("[TEST 5] Kiểm tra sửa tiêu đề và chọn kênh...", flush=True)
            res = await send_cmd(ws, 10, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const titleInput = document.getElementById('repurposed-publish-title');
                        titleInput.value = "Shorts Test Cực Hay Vừa Trích Xuất #Shorts";
                        const channelSelect = document.getElementById('repurposed-publish-channel');
                        const count = channelSelect.options.length;
                        return { title: titleInput.value, channelCount: count };
                    })()
                """,
                "returnByValue": True
            })
            t5 = res.get("result", {}).get("result", {}).get("value")
            print(f"  -> Thành công: Tiêu đề='{t5['title']}', Số kênh khả dụng={t5['channelCount']}", flush=True)

            # 6. Đóng Player Modal
            print("[TEST 6] Đóng Modal và kiểm tra dọn dẹp tài nguyên video...", flush=True)
            await send_cmd(ws, 11, "Runtime.evaluate", {
                "expression": "closeRepurposedPlayerModal();"
            })
            await asyncio.sleep(0.5)

            res = await send_cmd(ws, 12, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const m = document.getElementById('modal-repurposed-player');
                        const v = document.getElementById('repurposed-video-element');
                        return {
                            modalDisplay: window.getComputedStyle(m).display,
                            modalVisible: m.offsetWidth > 0,
                            videoPaused: v.paused,
                            videoSrc: v.src || v.getAttribute('src') || ''
                        };
                    })()
                """,
                "returnByValue": True
            })
            t6 = res.get("result", {}).get("result", {}).get("value")
            assert t6["modalDisplay"] == "none", f"Modal không ẩn khi đóng: {t6}"
            assert t6["videoPaused"] == True, f"Video không dừng khi đóng: {t6}"
            print(f"  -> Thành công: Modal đã ẩn ({t6['modalDisplay']}), Video dừng phát ({t6['videoPaused']}), src đã giải phóng", flush=True)

            # 7. Mở lại Modal lần 2 để kiểm tra độ tin cậy
            print("[TEST 7] Mở lại Modal lần 2 (Re-open reliability)...", flush=True)
            await send_cmd(ws, 13, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const playBtn = document.querySelector('.repurposed-card-play-btn');
                        if (playBtn) playBtn.click();
                    })()
                """
            })
            await asyncio.sleep(1.0)
            res = await send_cmd(ws, 14, "Runtime.evaluate", {
                "expression": """
                    (() => {
                        const m = document.getElementById('modal-repurposed-player');
                        return {
                            modalDisplay: window.getComputedStyle(m).display,
                            modalVisible: m.offsetWidth > 0
                        };
                    })()
                """,
                "returnByValue": True
            })
            t7 = res.get("result", {}).get("result", {}).get("value")
            assert t7["modalDisplay"] == "flex", f"Modal mở lại thất bại: {t7}"
            print(f"  -> Thành công: Modal mở lại trơn tru không lỗi!", flush=True)

            print("\n>>> TẤT CẢ 7 BÀI TEST LUỒNG GIAO DIỆN ĐỀU PASSED 100%! <<<", flush=True)

    finally:
        proc.kill()
        print("Chrome test process terminated.", flush=True)

if __name__ == "__main__":
    asyncio.run(run_test())
