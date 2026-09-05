# Miniclaude Terminal UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace line-oriented JSON logging with separated, bounded Rich panels for workflow stages, tool calls, tool results, and final status.

**Architecture:** Add a focused `cli/render.py` presentation module exposing `render_event(console, event)`. Keep `cli/app.py` responsible only for command setup, workflow iteration, exit codes, and forwarding events to the renderer; do not change the workflow event protocol.

**Tech Stack:** Python 3.11+, Rich, Typer, pytest, Ruff.

**Repository constraint:** Work directly in the current directory. Do not commit or push until the user explicitly requests it, and do not modify README.

---

### Task 1: Rendering primitives and separate tool panels

**Files:**
- Create: `src/miniclaude/cli/render.py`
- Create: `tests/test_cli_render.py`

- [x] **Step 1: Write failing tests for separate tool panels**

Create a captured Rich console and assert that a call and its result have separate titles, multiline values, success/failure styling-compatible labels, and no escaped single-line payload:

```python
from io import StringIO

from rich.console import Console

from miniclaude.cli.render import render_event


def rendered(*events: dict, width: int = 100) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=width)
    for event in events:
        render_event(console, event)
    return output.getvalue()


def test_tool_call_and_result_are_separate_panels():
    output = rendered(
        {
            "type": "tool_call",
            "name": "FileWriteTool",
            "args": {"file_path": "hello.py", "content": "print('one')\nprint('two')"},
        },
        {
            "type": "tool_result",
            "name": "FileWriteTool",
            "result": {"ok": True, "path": "hello.py"},
        },
    )

    assert "Tool Call - FileWriteTool" in output
    assert "Tool Result - FileWriteTool" in output
    assert "print('one')\nprint('two')" in output
    assert output.index("Tool Call") < output.index("Tool Result")
```

- [x] **Step 2: Run the tool-panel test and verify RED**

Run:

```powershell
uv run --locked pytest tests/test_cli_render.py::test_tool_call_and_result_are_separate_panels -q
```

Expected: collection fails because `miniclaude.cli.render` does not exist.

- [x] **Step 3: Write failing tests for bounded and literal display**

Add tests which render a 2,500-character `stdout`, assert a notice such as `display truncated - original 2500 characters`, ensure the visible body is shorter than the original, and assert text containing `[bold]literal[/bold]` remains literal in captured output.

```python
def test_long_tool_output_is_truncated_with_original_length():
    output = rendered(
        {
            "type": "tool_result",
            "name": "ShellTool",
            "result": {"ok": True, "stdout": "x" * 2500, "stderr": ""},
        }
    )
    assert "display truncated - original 2500 characters" in output
    assert "x" * 2500 not in output


def test_untrusted_markup_is_rendered_literally():
    output = rendered({"type": "ai_message", "content": "[bold]literal[/bold]"})
    assert "[bold]literal[/bold]" in output
```

- [x] **Step 4: Implement minimal rendering primitives and tool panels**

Implement constants and helpers in `render.py`:

```python
DISPLAY_LIMIT = 2000


def _bounded(value: object, limit: int = DISPLAY_LIMIT) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... display truncated - original {len(text)} characters"


def _section(label: str, value: object) -> Group:
    return Group(Text(label, style="bold"), Text(_bounded(value)))


def _tool_call(event: dict) -> Panel:
    sections = [_section(str(key), value) for key, value in event.get("args", {}).items()]
    return Panel(
        Group(*sections) if sections else Text("No arguments"),
        title=Text(f"Tool Call - {event.get('name', 'unknown')}") ,
        border_style="magenta",
    )


def _tool_result(event: dict) -> Panel:
    result = event.get("result", {})
    ok = bool(result.get("ok")) if isinstance(result, dict) else False
    values = result.items() if isinstance(result, dict) else [("result", result)]
    return Panel(
        Group(*[_section(str(key), value) for key, value in values]),
        title=Text(f"Tool Result - {event.get('name', 'unknown')}") ,
        border_style="green" if ok else "red",
    )
```

Add `render_event` dispatch for `tool_call`, `tool_result`, and `ai_message`, always constructing `Text` objects rather than enabling markup.

- [x] **Step 5: Run renderer tests and verify GREEN**

Run:

```powershell
uv run --locked pytest tests/test_cli_render.py -q
```

Expected: all Task 1 renderer tests pass.

### Task 2: Workflow stage panels and CLI delegation

**Files:**
- Modify: `src/miniclaude/cli/render.py`
- Modify: `src/miniclaude/cli/app.py`
- Modify: `tests/test_cli_render.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing tests for stage hierarchy**

Add parameterized renderer tests for `planner`, `actor`, `verifier`, and `final`. Verify titles and human-readable content:

```python
def test_stage_events_have_distinct_panels():
    output = rendered(
        {
            "type": "planner",
            "attempt": 1,
            "plan_summary": "Write tests first",
            "todos": [{"id": "tests", "content": "Write tests", "status": "in_progress"}],
            "acceptance_criteria": ["tests pass"],
            "verification_commands": ["pytest -q"],
        },
        {"type": "actor", "attempt": 1, "ok": True, "summary": "implemented", "todos": []},
        {
            "type": "verifier",
            "attempt": 1,
            "passed": True,
            "reason": "checks pass",
            "checks": [{"name": "tests pass", "passed": True, "detail": "pytest passed"}],
            "results": [],
        },
        {"type": "final", "passed": True, "content": "verified"},
    )

    assert "[planner] - Attempt 1" in output
    assert "[actor] - Attempt 1" in output
    assert "[verifier] - Attempt 1 - PASSED" in output
    assert "[final] - SUCCESS" in output
    assert "[ok] tests pass" in output
```

Add failure variants asserting `FAILED`, `FAILURE`, and `[x]`; add an unknown-event test asserting `Unsupported event: mystery` without dumping arbitrary payload fields.

- [x] **Step 2: Run the stage-panel tests and verify RED**

Run:

```powershell
uv run --locked pytest tests/test_cli_render.py -q
```

Expected: new stage assertions fail because dispatch is not implemented.

- [x] **Step 3: Implement stage renderers**

Add small helpers `_planner`, `_actor`, `_verifier`, `_final`, `_todo_lines`, `_check_lines`, and `_command_result_sections`. Use blue/cyan/green/yellow/red borders and literal `Text` values. Render list entries one per line with `-`, successful checks with `[ok]`, and failed checks with `[x]` so the output remains safe on GBK Windows terminals.

Dispatch all current event types in one public function:

```python
def render_event(console: Console, event: dict) -> None:
    kind = event.get("type", "unknown")
    if kind == "react_event":
        render_event(console, event.get("event", {}))
        return
    renderer = EVENT_RENDERERS.get(kind)
    if renderer is None:
        console.print(Text(f"Unsupported event: {kind}", style="dim"))
        return
    console.print(renderer(event))
```

Include compact renderers for `run_start`, `model_start`, `ai_message`, `final_answer`, and sanitized `error` events.

- [x] **Step 4: Replace app-local rendering with module delegation**

In `cli/app.py`, remove `json`, `_show`, and `_show_stage_event`; import `render_event` and call it inside the existing workflow loop:

```python
from miniclaude.cli.render import render_event

# inside main
for event in stream_workflow_events(...):
    render_event(console, event)
    if event["type"] == "final":
        saw_final = True
        failed = not event["passed"]
```

Do not change workflow arguments, error handling, exit codes, or shell warning behavior.

- [x] **Step 5: Update CLI integration assertions**

Change only presentation assertions in `tests/test_cli.py` to require the new titles and separated tool panels while retaining existing checks for workspace creation, option forwarding, successful/failing exit codes, and secret sanitization.

```python
assert "[planner] - Attempt 1" in result.output
assert "[actor] - Attempt 1" in result.output
assert "[verifier] - Attempt 1 - PASSED" in result.output
assert "[final] - SUCCESS" in result.output
assert "Acceptance criteria" in result.output
assert "Verification commands" in result.output
assert "Checks" in result.output
```

- [x] **Step 6: Run focused CLI tests and verify GREEN**

Run:

```powershell
uv run --locked pytest tests/test_cli_render.py tests/test_cli.py -q
```

Expected: renderer and CLI tests pass with unchanged exit-code behavior.

### Task 3: Regression verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-09-05-terminal-ui.md`

- [x] **Step 1: Run the complete locked test suite**

Run:

```powershell
uv run --locked pytest -q --tb=short
```

Expected: all offline tests pass; live API tests remain explicitly skipped.

- [x] **Step 2: Run static and formatting checks**

Run:

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
git diff --check
```

Expected: every command exits with code 0.

- [x] **Step 3: Inspect captured terminal output**

Run the renderer tests with captured output available on failure and inspect one deterministic sample at an 80–100 column width. Confirm panel titles remain visible, Tool Call and Tool Result are separate, multiline output is readable, and truncation is explicit.

- [x] **Step 4: Confirm scope constraints**

Run:

```powershell
git diff --name-only -- README.md
git ls-files .env '.miniclaude/**'
git status --short
```

Expected: README diff is empty; `.env` and runtime workspace files are not tracked; UI work remains uncommitted.

- [x] **Step 5: Report the exact verification results**

Summarize changed files, test counts, lint/format status, visual behavior, and the uncommitted state. Do not commit or push until the user explicitly requests it.
