---
name: biweekly-system-model-upgrader
description: >-
  Every 2 weeks (14 days) or on demand, automatically audit all systems, IDE extensions,
  agent optimization files (AGENTS.md, SKILL.md, MCP configs), and workspace projects
  to align with the newest AI models and technologies. MUST present a detailed upgrade
  report and receive user approval BEFORE applying any changes.
---

# Biweekly System, Agent & Project Technology Upgrader (`biweekly-system-model-upgrader`)

This skill defines the periodic (14-day cycle) inspection and modernization workflow for the entire `d:\Python` workspace (including **AI YouTube Studio** at `d:\Python\Youtube`, agent skills/rules, and IDE tooling).

> [!IMPORTANT]
> **MANDATORY RULE — REPORT BEFORE EXECUTING (BÁO CÁO TRƯỚC KHI THỰC HIỆN):**
> You **MUST NEVER** modify project code, model IDs, dependencies, or agent configurations silently during this workflow. You **MUST** complete Phase 1 & Phase 2, present a clear Vietnamese report to the user, and **wait for explicit user confirmation** before executing Phase 3.

---

## Phase 1: 14-Day Cycle Check & Multi-Layer System Audit (Kiểm tra chu kỳ & Quét toàn hệ thống)

1. **Check Audit Timestamp (`last_system_audit.json`)**:
   - Read `d:\Python\.agents\last_system_audit.json`.
   - Check if `>= 14 days` have elapsed since `last_audit_date` (or if the user explicitly requested an audit/upgrade).
2. **Research Latest AI Models & Ecosystem Updates (Cập nhật công nghệ & Model mới nhất)**:
   - Use `search_web` to check the latest released models and SDK updates across:
     - **Google Gemini** (e.g., Gemini 4 / Argon, latest Flash / Pro / Image / TTS / Veo models, `google-genai` SDK).
     - **Anthropic Claude & OpenAI / OSS** (latest Sonnet, Opus, GPT/OSS reasoning & coding models).
     - **Core Frameworks**: LangGraph, LangChain, FastAPI, Playwright, Edge-TTS / Kokoro / VieNeu-TTS, FFmpeg pipelines.
3. **Audit 3 System Layers (Quét 3 tầng hệ thống)**:
   - **Layer A — Agent Optimization Files (`AGENTS.md`, `SKILL.md`, `mcp_config.json`)**:
     - Inspect `d:\Python\AGENTS.md`, `d:\Python\Youtube\AGENTS.md`, and all `SKILL.md` files under `.agents/skills/`.
     - Identify outdated instructions, deprecated tool patterns, or new best practices for prompt/context optimization and subagent orchestration.
   - **Layer B — Workspace Projects (`d:\Python\Youtube` & sibling projects)**:
     - Scan configuration and agent files (e.g., `app/core/config.py`, `app/agents/`, `app/services/`, `app/graph/`, `requirements.txt`) for hardcoded/deprecated model IDs, outdated API patterns, or performance bottlenecks.
   - **Layer C — IDE Extensions & Local Tooling**:
     - Verify local helper extensions (such as `jlcodes.antigravity-cockpit`) and MCP servers to ensure new model families/quotas are properly recognized and not filtered out by legacy regexes.

---

## Phase 2: Mandatory Pre-Upgrade Report (Báo cáo chi tiết & Xin phép người dùng)

Before touching any file, present a structured report in **Vietnamese** containing:

1. **Tóm tắt Công nghệ & Model Mới Nhất**:
   - Các model AI mới ra mắt hoặc thay đổi từ nhà cung cấp (Google Gemini, Claude, OpenAI, TTS/Video...) so với cấu hình hiện tại.
2. **Đề xuất Nâng cấp File Tối ưu cho Agent (`AGENTS.md` / `SKILL.md` / Tooling)**:
   - Liệt kê từng file cấu hình Agent hoặc Extension cần cập nhật và lý do tối ưu.
3. **Đề xuất Nâng cấp cho Project (`d:\Python\Youtube` & các project khác)**:
   - Bảng so sánh: `File / Module` | `Cấu hình / Model hiện tại` | `Đề xuất nâng cấp` | `Lợi ích & Mức độ ảnh hưởng`.
4. **Xin xác nhận từ Người dùng**:
   - Hỏi rõ người dùng muốn **Thực hiện toàn bộ**, **Chọn từng phần**, hay **Bỏ qua** đợt nâng cấp này.

---

## Phase 3: Execution, Verification & Git Sync (Thực thi sau khi được duyệt)

**ONLY after the user approves:**

1. **Apply Approved Upgrades**:
   - Update the approved Agent files (`AGENTS.md`, `SKILL.md`, MCP/extension configs) and Project source/config files.
2. **Update Audit Tracker**:
   - Update `last_audit_date` and `next_due_date` (+14 days) in `d:\Python\.agents\last_system_audit.json`, recording a brief summary of applied upgrades.
3. **Optimize, Verify & Push**:
   - Run syntax/import checks (`python -m py_compile`, smoke tests) and execute the [auto-optimize-merge-push](../auto-optimize-merge-push/SKILL.md) workflow to commit and push all project updates cleanly to `origin/main`.
