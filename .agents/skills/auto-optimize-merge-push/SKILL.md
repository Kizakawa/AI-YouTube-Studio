---
name: auto-optimize-merge-push
description: >-
  Automatically optimize modified code, verify syntax and runtime imports, safely sync/merge branches,
  and commit & push changes to GitHub (origin/main) immediately after completing any coding, bugfix,
  or configuration task. Use this skill automatically at the end of every code modification workflow.
---

# Auto-Optimize, Merge & Push to GitHub Skill

This skill defines the mandatory post-implementation workflow for **AI YouTube Studio** (`d:\AI_YouTube_Project_Ready\AI-youtube-main`). Whenever you finish writing, refactoring, or fixing code/configuration files, you **MUST** execute this workflow automatically before concluding your response to the user.

---

## 1. Code Optimization & Quality Gate (Tự động tối ưu & Kiểm định Code)

Before staging any files, review and optimize all modified files:

1. **Inspect Changes**:
   - Run `git status --short` to list all modified and untracked files.
2. **Optimize & Clean Up**:
   - Remove unused imports, dead code, redundant loops, or leftover debug `print()` statements.
   - Ensure backward-compatible function signatures (e.g., defensive `**kwargs` on pipeline/service methods called by multiple LangGraph nodes).
   - Keep FFmpeg/IO operations single-pass and non-blocking where applicable.
3. **Syntax & Import Verification**:
   - Run Python bytecode compilation on all modified `.py` files to guarantee zero syntax or indentation errors:
     ```powershell
     python -m py_compile <list_of_modified_py_files>
     ```
   - Run a quick one-line import & smoke check (`python -c "import ..."`) on the modified modules to verify there are no circular imports or missing symbols.
4. **Secret & Artifact Hygiene**:
   - **NEVER** stage or commit sensitive or runtime-generated files: `.env`, `*.pickle`, `client_secret*.json`, `*.db`, `*.sqlite`, `output/`, `logs/`, `temp/`, or `__pycache__/`.

---

## 2. Safe Branch Sync & Merge (Đồng bộ & Merge nhánh an toàn)

1. **Branch Check & Merge**:
   - Check the current branch via `git branch --show-current`.
   - If working on a feature or subagent branch, switch to `main` and merge cleanly:
     ```powershell
     git checkout main
     git merge <feature-branch>
     ```
2. **Remote Synchronization**:
   - Stage your verified changes first (`git add <specific_files>`).
   - If remote `origin/main` has new commits, rebase or merge safely:
     ```powershell
     git fetch origin main
     git pull --rebase origin main
     ```
   - If any merge conflict occurs, resolve it intelligently by preserving both the remote updates and your newly optimized logic, then re-run `python -m py_compile` on conflicted files.

---

## 3. Automated Commit & Push to GitHub (Tự động Commit & Push)

1. **Stage Verified Files**:
   - Explicitly stage all modified source and configuration files:
     ```powershell
     git add <file1> <file2> ...
     ```
2. **Conventional Commit Message**:
   - Commit using a clear, descriptive **Conventional Commits** message (`feat(scope): ...`, `fix(scope): ...`, `perf(scope): ...`, `refactor(scope): ...`):
     ```powershell
     git commit -m "<type>(<scope>): <concise summary of optimizations and changes>"
     ```
3. **Push to `origin/main`**:
   - Push directly to GitHub:
     ```powershell
     git push origin main
     ```
4. **Final Verification & Reporting**:
   - Verify clean status and latest commit hash via `git log -n 1 --oneline`.
   - Include the commit hash (e.g., `` `abc1234` ``) in your final Vietnamese summary to the user so they know their code is already optimized, merged, and live on GitHub.
