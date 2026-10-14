# Project Agent Rules — AI YouTube Studio

## Mandatory Post-Task Workflow: Auto-Optimize, Merge & Push (`auto-optimize-merge-push`)
- Whenever you complete any code modification, bugfix, refactoring, or configuration update in `d:\Python\Youtube`, you **MUST** automatically follow the [auto-optimize-merge-push](.agents/skills/auto-optimize-merge-push/SKILL.md) skill before finishing your turn:
  1. **Optimize & Verify**: Clean up dead code/imports, ensure backward-compatible method signatures, and verify all modified Python files using `python -m py_compile` plus a quick import/smoke test.
  2. **Safe Sync & Merge**: Ensure all changes are merged cleanly onto the `main` branch without conflicts (excluding secrets, `.env`, `.db`, `*.pickle`, and runtime outputs).
  3. **Auto Commit & Push**: Stage modified files (`git add`), commit with a clear Conventional Commit message (`feat/fix/perf/refactor`), and push directly to GitHub (`git push origin main`) without waiting for the user to ask.

