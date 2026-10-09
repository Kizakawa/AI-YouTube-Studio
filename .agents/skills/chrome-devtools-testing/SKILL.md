---
name: chrome-devtools-testing
description: >-
  Workflows and instructions for automated UI/UX and logic testing of the AI YouTube Studio
  dashboard (http://localhost:8000) using Chrome DevTools MCP. Use when testing user interface
  components, inspecting console logs, verifying network API calls, and evaluating browser performance.
---

# Chrome DevTools UI/UX & Logic Testing Guide

This skill guides automated testing of the **AI YouTube Studio** web interface using the **Chrome DevTools MCP** server.

---

## 1. Prerequisites
- **Local Dev Server**: The FastAPI backend must be running on `http://localhost:8000` (e.g. via `python run.py`).
- **Google Chrome**: Installed at `C:\Program Files\Google\Chrome\Application\chrome.exe`.
- **MCP Server**: Registered in `mcp_config.json` via `npx -y chrome-devtools-mcp@latest`.

---

## 2. Core Testing Workflows

### A. Dashboard Health Check & Console Error Inspection
1. **Navigate to App**: Open `http://localhost:8000` in the browser.
2. **Console Inspection**: Check for any JavaScript errors or uncaught exceptions in DevTools console.
3. **Network Tab**: Verify that initial assets (`dashboard.js`, `index.html`, style sheets) load with HTTP 200.

### B. Video Repurposer UI Testing
1. Navigate to the **Repurposer** view.
2. Verify UI elements:
   - YouTube URL input field.
   - Crop Focus dropdown (`Center`, `Left (Khách mời/Khung trái)`, `Right (MC/Khung phải)`).
   - Audio Mode radio buttons (`Original` vs `AI Voiceover`).
3. Trigger a test action and inspect the network request sent to `/api/repurpose/analyze` or `/api/repurpose/render`.

### C. Video Library & Storyboard Inspection
1. Navigate to the **Video Library** tab (`/` with Library modal).
2. Check that the search bar and channel filters trigger correct requests.
3. Verify that the table renders status badges, thumbnails, and action buttons (`Shot Table`, `Storyboard`, `Render`).

### D. Screenshot & Visual Verification
1. Capture viewport screenshots to verify layout alignment on mobile (9:16) and desktop viewports.
2. Confirm typography, contrast, and dark mode readability.
