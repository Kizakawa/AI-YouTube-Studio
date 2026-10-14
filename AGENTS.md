# Project Agent Rules — AI YouTube Studio

## 1. Mandatory Post-Task Workflow: Auto-Optimize, Merge & Push (`auto-optimize-merge-push`)
- Whenever you complete any code modification, bugfix, refactoring, or configuration update in `d:\Python\Youtube`, you **MUST** automatically follow the [auto-optimize-merge-push](.agents/skills/auto-optimize-merge-push/SKILL.md) skill before finishing your turn:
  1. **Optimize & Verify**: Clean up dead code/imports, ensure backward-compatible method signatures, and verify all modified Python files using `python -m py_compile` plus a quick import/smoke test.
  2. **Safe Sync & Merge**: Ensure all changes are merged cleanly onto the `main` branch without conflicts (excluding secrets, `.env`, `.db`, `*.pickle`, and runtime outputs).
  3. **Auto Commit & Push**: Stage modified files (`git add`), commit with a clear Conventional Commit message (`feat/fix/perf/refactor`), and push directly to GitHub (`git push origin main`) without waiting for the user to ask.

## 2. Periodic 14-Day System, Agent & Model Upgrade Check (`biweekly-system-model-upgrader`)
- Every **2 weeks (14 days)** — tracked via [d:\Python\.agents\last_system_audit.json](../.agents/last_system_audit.json) — or whenever requested by the user, follow the [biweekly-system-model-upgrader](.agents/skills/biweekly-system-model-upgrader/SKILL.md) skill:
  1. **Audit System, Agent & Project**: Scan for the newest AI models (Gemini, Claude, OpenAI/OSS, TTS/Image/Video), library updates, IDE extensions, Agent optimization files (`AGENTS.md`, `SKILL.md`, `mcp_config.json`), and `d:\Python\Youtube` configurations (`app/core/config.py`, `app/agents/`, `app/services/`, `app/graph/`).
  2. **Mandatory Report-Before-Execute Gate (Báo cáo trước khi thực hiện)**: **NEVER** apply model/technology upgrades silently. Always present a detailed comparison report in Vietnamese to the user first and wait for confirmation before executing changes.


