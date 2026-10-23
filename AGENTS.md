# Project Agent Rules — AI YouTube Studio

## 1. Mandatory Post-Task Workflow: Auto-Optimize, Merge & Push (`auto-optimize-merge-push`)
- Whenever you complete any code modification, bugfix, refactoring, or configuration update in `d:\AI_YouTube_Project_Ready\AI-youtube-main`, you **MUST** automatically follow the [auto-optimize-merge-push](.agents/skills/auto-optimize-merge-push/SKILL.md) skill before finishing your turn:
  1. **Optimize & Verify**: Clean up dead code/imports, ensure backward-compatible method signatures, and verify all modified Python files using `python -m py_compile` plus a quick import/smoke test.
  2. **Safe Sync & Merge**: Ensure all changes are merged cleanly onto the `main` branch without conflicts (excluding secrets, `.env`, `.db`, `*.pickle`, and runtime outputs).
  3. **Auto Commit & Push**: Stage modified files (`git add`), commit with a clear Conventional Commit message (`feat/fix/perf/refactor`), and push directly to GitHub (`git push origin main`) without waiting for the user to ask.

## 2. Periodic 14-Day System, Agent & Model Upgrade Check (`biweekly-system-model-upgrader`)
- Every **2 weeks (14 days)** — tracked via [d:\AI_YouTube_Project_Ready\.agents\last_system_audit.json](../.agents/last_system_audit.json) — or whenever requested by the user, follow the [biweekly-system-model-upgrader](.agents/skills/biweekly-system-model-upgrader/SKILL.md) skill:
  1. **Audit System, Agent & Project**: Scan for the newest AI models (Gemini, Claude, OpenAI/OSS, TTS/Image/Video), library updates, IDE extensions, Agent optimization files (`AGENTS.md`, `SKILL.md`, `mcp_config.json`), and `d:\AI_YouTube_Project_Ready\AI-youtube-main` configurations (`config/settings.py`, `app/agents/`, `app/services/`, `app/graph/`).
  2. **Mandatory Report-Before-Execute Gate (Báo cáo trước khi thực hiện)**: **NEVER** apply model/technology upgrades silently. Always present a detailed comparison report in Vietnamese to the user first and wait for confirmation before executing changes.

## 3. Mandatory 100% Real YouTube Metrics Reporting (`youtube-real-metrics-reporting`)
- Whenever the user asks for a summary, status check, or performance analysis of uploaded YouTube videos or channels, you **MUST** follow the [youtube-real-metrics-reporting](.agents/skills/youtube-real-metrics-reporting/SKILL.md) skill:
  1. **Zero Estimation Policy**: **NEVER** report estimated/interpolated retention metrics (`APV %`, `Stayed %`, `Swipe Away %`, `AVD`). Only report 100% real numbers fetched from **YouTube Data API v3** and **YouTube Analytics API v2** (or user-provided YouTube Studio Realtime screenshots). If a video is `< 24–48h` old and YouTube Analytics API v2 has not finalized its row yet, explicitly display `⏳ Chờ Analytics chốt sổ (24–48h)`.
  2. **Mandatory Strategic Analysis**: Always accompany the real data tables with a data-driven analysis covering top-performing videos (velocity, `Like/View %`, real `APV %` / `Stayed %`), recovering channels, and actionable warnings based strictly on real metrics.

## 4. Mandatory System-Wide Root-Cause Bugfix & Anti-Overfitting (`systemic-root-cause-bugfix`)
- Whenever you debug, fix, or optimize any issue in `d:\AI_YouTube_Project_Ready\AI-youtube-main`, you **MUST** follow the [systemic-root-cause-bugfix](.agents/skills/systemic-root-cause-bugfix/SKILL.md) skill (incorporating GitHub's `obra/superpowers` `systematic-debugging`, `root-cause-tracing`, `defense-in-depth`, and Anti-Reward-Hacking guardrails):
  1. **Zero Test-Case Overfitting & Zero Keyword-Patching Policy**: **NEVER** patch bugs by hardcoding topic/species/biome/weather keyword lists (`if "snow" in ...`, `if "tiger" in ...`, etc.) or blindly appending static strings to queries just to pass a single test case. Every fix must pass the **4-Domain Counterfactual Test** (generalizing across Wildlife, Ancient History, Dark Psychology, and Sci-Fi/Ocean Mysteries without domain-specific keywords in Python).
  2. **Backward Root-Cause Tracing & Right-Layer Defense-in-Depth**: Trace bugs backward from the symptom to the upstream source (`FFmpeg` ⬅ `VisualCritic/RAG` ⬅ `StockFetcher` ⬅ `VisualCurationAgent` ⬅ `Query Normalization` ⬅ `System Prompt`) and fix the root cause across all parallel flows (`HookVideoNode`, `VisualCurationAgent` batch & single scene, video & photo curation, and topic-agnostic CV/FFmpeg gates).

## 5. Universal Engineering & AI System Guardrails (All Projects)
- Always adhere to the universal engineering skills in `.agents/skills/` (and `~/.gemini/config/skills/`):
  - **[verification-before-completion](.agents/skills/verification-before-completion/SKILL.md)**: Never claim a task or bugfix is complete without running fresh verification (`py_compile`, smoke tests, output inspection) after the final code edit.
  - **[architecture-first-design](.agents/skills/architecture-first-design/SKILL.md)**: Keep modules focused (Single Responsibility, no God-files), preserve backward-compatible signatures (`**kwargs`), and clean up dead code immediately.
  - **[llm-agentic-system-design](.agents/skills/llm-agentic-system-design/SKILL.md)**: Keep semantic reasoning in LLM System Prompts / Multimodal Vision (preferring Batch Prompting over sequential loops) and keep deterministic validation/math/key-rotation in code.
  - **[subagent-parallel-execution](.agents/skills/subagent-parallel-execution/SKILL.md)** & **[security-and-secret-hygiene](.agents/skills/security-and-secret-hygiene/SKILL.md)**: Execute independent tasks in parallel without polling loops, and never hardcode or stage secrets/runtime artifacts (`.env`, `*.pickle`, `*.db`).
