# Stage Two Plan-Execute-Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested LangGraph outer loop that plans, executes with the stage-one ReAct agent, independently verifies, retries, and reports a deterministic result.

**Architecture:** A typed `StateGraph` connects planner, actor, verifier, and final nodes. Models and command execution are dependency-injected so unit tests exercise real graph routing and workspace tools without network access.

**Tech Stack:** Python 3.11+, LangGraph 1.x, LangChain Core/OpenAI, Typer, pytest, Ruff.

---

### Task 1: Graph state and Todo behavior

**Files:**
- Create: `src/miniclaude/graph/__init__.py`
- Create: `src/miniclaude/graph/state.py`
- Create: `src/miniclaude/tools/todo_tools.py`
- Modify: `src/miniclaude/tools/registry.py`
- Test: `tests/test_graph_state.py`

- [x] Write tests proving graph input defaults, Todo validation, plan publication, legal status transitions, and unknown Todo errors.
- [x] Run `uv run --locked pytest tests/test_graph_state.py -q` and confirm imports or behavior fail for the missing feature.
- [x] Implement typed state models and state-bound Todo tools with no filesystem persistence.
- [x] Run the focused test and the existing tool suite until green.

### Task 2: Planner and deterministic verification commands

**Files:**
- Create: `src/miniclaude/prompts/__init__.py`
- Create: `src/miniclaude/prompts/stage2.py`
- Create: `src/miniclaude/graph/nodes.py`
- Test: `tests/test_graph_nodes.py`

- [x] Write tests for valid structured plans, retry context, command result capture, output bounds, and invalid model output.
- [x] Run `uv run --locked pytest tests/test_graph_nodes.py -q` and confirm the new tests fail for missing nodes.
- [x] Implement Pydantic planner/verifier schemas, prompt construction, and injected node factories.
- [x] Run the focused node tests and preserve all stage-one tests.

### Task 3: LangGraph routing and retry loop

**Files:**
- Create: `src/miniclaude/graph/workflow.py`
- Modify: `src/miniclaude/core/agent.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_workflow.py`

- [x] Write graph tests for success routing, failure → replan routing, and max-attempt termination.
- [x] Run `uv run --locked pytest tests/test_workflow.py -q` and confirm failure because the workflow is absent.
- [x] Add the constrained LangGraph dependency and implement `START → planner → actor → verifier → final`, including custom events.
- [x] Refactor the stage-one agent only enough to accept Actor prompt/tool customization while keeping its public behavior compatible.
- [x] Run workflow and stage-one agent tests until green.

### Task 4: CLI integration

**Files:**
- Modify: `src/miniclaude/cli/app.py`
- Test: `tests/test_cli.py`

- [x] Add failing tests for the default graph path, `--max-attempts`, stage labels, successful exit, and exhausted retry exit.
- [x] Run the focused CLI tests and confirm the expected failures.
- [x] Switch the CLI to stage-two streaming and render bounded Planner/Actor/Verifier/Final events.
- [x] Run CLI tests until green and confirm `uv run --locked miniclaude --help` documents the new option.

### Task 5: Stage-two acceptance case and documentation

**Files:**
- Create: `tests/test_stage2_live.py`
- Create: `docs/stage2.md`
- Modify: `pyproject.toml`

- [x] Add an opt-in live test specifying a headless, finite Conway's Game of Life implementation with generated unit tests and a demo command.
- [x] Run the test without its opt-in flag and confirm it skips cleanly.
- [x] Document installation, graph flow, CLI usage, DeepSeek-compatible configuration, and the Game of Life acceptance command without expanding README.
- [x] Run the full verification suite: `pytest`, Ruff check, Ruff format check, and CLI help.

### Task 6: Review and handoff

- [x] Inspect `git diff --check`, ensure no runtime workspace or secrets are tracked, and review every changed file.
- [x] Request an independent code review and address only verified findings with regression tests first.
- [x] Re-run the full verification suite and report the exact results.
- [x] Leave changes uncommitted until the user explicitly asks to commit and push.

### Task 7: Align graph state, messages, and Planner Todo publication

**Files:**
- Modify: `src/miniclaude/graph/state.py`
- Modify: `src/miniclaude/core/agent.py`
- Modify: `src/miniclaude/graph/nodes.py`
- Test: `tests/test_graph_state.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_graph_nodes.py`

- [x] Add a failing type test asserting `MiniclaudeGraphState.__total__ is False`.
- [x] Add a failing ReAct test passing `captured_messages=[]` and asserting the actual System, Human, AI, and Tool messages are recorded in execution order.
- [x] Add a failing graph test asserting Actor and Verifier message increments are merged into final state.
- [x] Add a failing Planner test that patches `TodoTracker.write` to fail if called directly and observes a successful `TodoWriteTool` execution through `execute_tool`.
- [x] Implement `total=False`, optional ReAct message capture, node message updates, and Planner TodoWrite execution through the tool registry boundary.
- [x] Run `uv run --locked pytest tests/test_graph_state.py tests/test_agent.py tests/test_graph_nodes.py tests/test_workflow.py -q` and confirm green.

### Task 8: Move workflow streaming into the core API

**Files:**
- Modify: `src/miniclaude/core/agent.py`
- Modify: `src/miniclaude/cli/app.py`
- Modify: `src/miniclaude/prompts/stage2.py`
- Modify: `src/miniclaude/graph/nodes.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_cli.py`

- [x] Add a failing test for `stream_workflow_events(task, ...)` that uses a fake compiled graph and asserts `stream_mode=["updates", "custom"]`.
- [x] Assert the core API converts graph updates to normalized `planner`, `actor`, `verifier`, and `final` events while forwarding only nested custom ReAct events.
- [x] Add a failing CLI test using the real core stream API boundary and asserting displayed Todo, acceptance criteria, verification commands, and checks.
- [x] Define `FINAL_PROMPT` with explicit success/failure templates and add a failing final-node test proving it is used deterministically.
- [x] Implement the core streaming API, simplify CLI to consume unified events, and format Final from `FINAL_PROMPT` without an LLM call.
- [x] Run `uv run --locked pytest tests/test_agent.py tests/test_cli.py tests/test_workflow.py -q` and confirm green.

### Task 9: Final verification and scope audit

- [x] Run the complete locked test suite; the paid `--run-live-stage2` case must remain skipped unless separately authorized.
- [x] Run Ruff lint and format checks, CLI help, `git diff --check`, and confirm no runtime workspace or `.env` is tracked.
- [x] Re-run a read-only specification audit and confirm the only remaining incomplete items are the explicitly deferred real DeepSeek Conway live acceptance and end-of-project README work.
- [x] Leave all changes uncommitted until the user explicitly requests commit and push.
