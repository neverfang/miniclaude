# README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the minimal README with an accurate, complete GitHub landing page for Miniclaude v0.6.0.

**Architecture:** Keep the documentation in one root `README.md`, organized from project positioning to setup, operation, internals, and safety. Derive current-behavior claims from the CLI, command registry, configuration example, stage documents, or tests, and explicitly label incomplete MCP support.

**Tech Stack:** GitHub-flavored Markdown, Python/uv command examples, Miniclaude CLI and Textual TUI.

---

### Task 1: Replace the README content

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the project header and positioning**

Use `# Miniclaude` as the title. Describe it as a Chinese learning project that incrementally builds a lightweight coding Agent inspired by Claude Code's interaction model, while clearly stating that it is not an Anthropic product.

- [ ] **Step 2: Document implemented capabilities and the six-stage roadmap**

Add a concise capability list and a table for ReAct, LangGraph, MultiAgent, Context Engineering, Harness Engineering, and terminal interaction. Mark all six stages complete at v0.6.0 without claiming full Claude Code parity.

- [ ] **Step 3: Add architecture and runtime flow**

Show the main packages under `src/miniclaude/` and the two execution paths:

```text
input -> Session context -> intent router -> chat
                                       \-> workflow -> Plan -> Execute -> Verify
```

Explain that workflow mode reuses tools, specialists, context management, checkpoints, trace, and approvals.

- [ ] **Step 4: Add installation and provider configuration**

Document Python 3.11+, uv, `uv sync`, copying `.env.example` to `.env`, and this OpenAI-compatible DeepSeek example:

```dotenv
OPENAI_API_KEY=your_deepseek_api_key
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_THINKING=disabled
TAVILY_API_KEY=your_tavily_api_key
```

State that Tavily is optional and only required for web research.

- [ ] **Step 5: Document startup modes and local data**

Include exact examples for `miniclaude`, `miniclaude -c`, `miniclaude --session`, one-shot tasks, `--resume`, Shell opt-in, and live approval. Explain `.miniclaude/sessions/<id>/workspace` and checkpoint workspaces without including a real local ID.

- [ ] **Step 6: Document interaction controls**

Add tables for Ctrl+C, Esc, Ctrl+L, Ctrl+N, Ctrl+O, Ctrl+S; slash commands from `/help` through `/exit`; and `inline`, `all`, `auto`, and `deny` approval modes. State that blocked commands remain blocked.

- [ ] **Step 7: Document Skills, MCP boundary, tests, safety, and references**

Explain project Skill discovery and activation through `/skills` and `/skill`, and state that `/mcp` currently reports status while actual MCP connections remain future work. Add offline and opt-in live test commands, safety limits, stage-document links, and the project plan link.

### Task 2: Validate documentation accuracy

**Files:**
- Verify: `README.md`
- Reference: `.env.example`
- Reference: `src/miniclaude/commands/registry.py`
- Reference: `src/miniclaude/cli/app.py`

- [ ] **Step 1: Check obsolete names, placeholders, and credential patterns**

Run:

```powershell
rg -n "mokioclaw|TBD|TODO|FIXME|sk-[A-Za-z0-9]" README.md
```

Expected: no matches.

- [ ] **Step 2: Check required content**

Run:

```powershell
rg -n "OPENAI_BASE_URL|TAVILY_API_KEY|--continue|--resume|/approve|/skills|/mcp|Ctrl\+C|415 passed" README.md
```

Expected: every required topic has at least one match.

- [ ] **Step 3: Check formatting and repository scope**

Run:

```powershell
git diff --check -- README.md
git status --short
```

Expected: no whitespace errors; only `README.md` is modified and the pre-existing untracked `PRODUCT.md` remains untracked.

### Task 3: Commit the README

**Files:**
- Commit: `README.md`

- [ ] **Step 1: Review the final diff**

Run:

```powershell
git diff -- README.md
```

Expected: a complete documentation replacement with no source-code changes.

- [ ] **Step 2: Commit only the README**

Run:

```powershell
git add -- README.md
git commit -m "docs: refresh project readme"
```

Expected: one documentation commit; `PRODUCT.md` is not staged.
