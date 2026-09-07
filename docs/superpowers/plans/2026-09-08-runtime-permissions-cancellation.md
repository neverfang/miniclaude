# Runtime Permissions and Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/approve` change the live Shell policy and make Ctrl+C/Escape cancel the complete active turn without leaving the TUI in `running`.

**Architecture:** Keep Slash parsing pure by returning typed policy actions, while the TUI owns process-local policy state. Introduce a thread-safe cancellation token shared by Session orchestration, workflow runtime, ReAct dispatch, and Bash; associate every TUI event with a run ID so late output is ignored. Persist cancellation on the user turn and terminate registered child-process trees cooperatively.

**Tech Stack:** Python 3.11+, Textual, LangChain/LangGraph, pytest, Ruff, `threading.Event`, `subprocess`.

---

## File map

The working tree already contains the completed readable-command-card change in
`src/miniclaude/cli/tui/widgets.py` and `tests/test_command_rendering.py`. Before Task 1,
verify and commit those two files as their own change. Keep the unrelated untracked
`PRODUCT.md` out of every commit. This establishes a clean baseline before Task 2 edits
`widgets.py` again.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_command_rendering.py -q
git add src/miniclaude/cli/tui/widgets.py tests/test_command_rendering.py
git commit -m "fix: render slash command results for humans"
```

- Create `src/miniclaude/core/cancellation.py`: thread-safe cancellation token and cancellation exception.
- Modify `src/miniclaude/commands/registry.py`: `/approve` parsing, `/approvals` alias, and typed actions.
- Modify `src/miniclaude/cli/tui/state.py`: runtime policy fields plus `cancelling`/`idle` transitions.
- Modify `src/miniclaude/cli/tui/widgets.py`: render Shell and approval policy in the Session sidebar.
- Modify `src/miniclaude/cli/tui/app.py`: apply policy actions, own active run IDs/tokens, bind Escape, reject stale events, and recover the prompt immediately.
- Modify `src/miniclaude/core/state.py`: attach cancellation and run identity to each runtime.
- Modify `src/miniclaude/tools/registry.py`: stop dispatching tools after cancellation.
- Modify `src/miniclaude/tools/bash_tool.py`: poll for cancellation and terminate the process tree.
- Modify `src/miniclaude/core/agent.py`: pass the token into workflow runtime and check it around model/tool boundaries.
- Modify `src/miniclaude/core/session.py`: persist cancelled user turns without fabricating an assistant answer.
- Modify `src/miniclaude/core/session_controller.py`: support replacement after cancellation and prevent cancelled output from being saved.
- Modify `src/miniclaude/core/harness.py`: finalize checkpoint/trace state as `cancelled` exactly once.
- Modify `src/miniclaude/core/trace.py`: count and summarize cancellation events.
- Create `tests/test_cancellation.py`: cancellation-token contract.
- Modify `tests/test_commands.py`, `tests/test_tui_state.py`, `tests/test_tui_app.py`, `tests/test_tools.py`, `tests/test_agent.py`, `tests/test_session.py`, `tests/test_session_controller.py`, `tests/test_harness.py`, and `tests/test_trace.py`: regression and integration coverage.

### Task 1: Turn `/approve` into a live policy command

**Files:**
- Modify: `src/miniclaude/commands/registry.py`
- Modify: `tests/test_commands.py`

- [ ] **Step 1: Replace the explanatory approval test with failing action tests**

Add these tests to `tests/test_commands.py`:

```python
import pytest


@pytest.mark.parametrize("mode", ["all", "inline", "auto", "deny"])
def test_approve_returns_typed_policy_action(mode):
    result = build_command_registry().execute(f"/approve {mode}", context())

    assert result.ok is True
    assert result.action == f"approval-mode:{mode}"
    assert mode in result.message


def test_approve_without_mode_reports_current_policy():
    result = build_command_registry().execute("/approve", context())

    assert result.ok is True
    assert result.action is None
    assert "disabled" in result.message
    assert "inline" in result.message
    assert all(mode in result.message for mode in ("all", "auto", "deny"))


def test_approvals_is_alias_and_invalid_mode_fails_locally():
    registry = build_command_registry()

    alias = registry.execute("/approvals all", context())
    invalid = registry.execute("/approve forever", context())

    assert alias.action == "approval-mode:all"
    assert invalid.ok is False
    assert invalid.action is None
    assert "all, inline, auto, deny" in invalid.message


def test_approve_cannot_change_policy_during_active_turn():
    result = build_command_registry().execute("/approve auto", context(active=True))

    assert result.ok is False
    assert result.action is None
    assert "running" in result.message.lower()
```

- [ ] **Step 2: Run the command tests and confirm the new behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands.py -q`

Expected: failures because `/approve` is unknown and `/approvals` does not return policy actions.

- [ ] **Step 3: Implement the pure command handler**

Replace the existing `approvals` handler and registration in `src/miniclaude/commands/registry.py` with:

```python
    def approve(context: CommandContext, arguments: str) -> CommandResult:
        mode = arguments.strip().casefold()
        if not mode:
            state = "enabled" if context.allow_shell else "disabled"
            return _result(
                f"Shell: {state}\n"
                f"Approval mode: {context.approval_mode}\n"
                "Modes: all, inline, auto, deny"
            )
        if mode not in {"all", "inline", "auto", "deny"}:
            return CommandResult(False, "Usage: /approve <all, inline, auto, deny>")
        shell = "disabled" if mode == "deny" else "enabled"
        return _result(
            f"Shell is now {shell}; approval mode is {mode}.",
            f"approval-mode:{mode}",
        )

    register(
        "approve",
        "Show or change Shell approval policy.",
        approve,
        usage="/approve [mode]",
        aliases=("approvals",),
        allowed_while_active=False,
    )
```

Remove the old standalone `/approvals` registration so help displays one canonical command.

- [ ] **Step 4: Run the focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the command contract**

```powershell
git add src/miniclaude/commands/registry.py tests/test_commands.py
git commit -m "feat: make approve a runtime policy command"
```

### Task 2: Store and display the process-local policy

**Files:**
- Modify: `src/miniclaude/cli/tui/state.py`
- Modify: `src/miniclaude/cli/tui/widgets.py`
- Modify: `src/miniclaude/cli/tui/app.py`
- Modify: `tests/test_tui_state.py`
- Modify: `tests/test_tui_app.py`

- [ ] **Step 1: Write failing reducer and TUI tests**

Add to `tests/test_tui_state.py`:

```python
def test_sidebar_reducer_tracks_runtime_policy():
    state = initial_session_view(
        "abc123def456",
        Path("workspace"),
        shell_enabled=False,
        approval_mode="inline",
    )

    state = reduce_session_event(
        state,
        {"type": "runtime_policy", "shell_enabled": True, "approval_mode": "all"},
    )

    assert state.shell_enabled is True
    assert state.approval_mode == "all"
```

Add to `tests/test_tui_app.py`:

```python
def test_slash_approve_changes_only_live_runtime_policy(tmp_path):
    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
            workflow_options={"allow_shell": False, "approval_mode": "inline"},
        )
        async with app.run_test() as pilot:
            await submit(pilot, "/approve all")
            assert app.workflow_options["allow_shell"] is True
            assert app.workflow_options["approval_mode"] == "all"
            sidebar = app.query_one("#session-sidebar").render().plain
            assert "shell       enabled" in sidebar
            assert "approval    all" in sidebar

            await submit(pilot, "/approve deny")
            assert app.workflow_options["allow_shell"] is False
            assert app.workflow_options["approval_mode"] == "deny"

    asyncio.run(scenario())
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tui_state.py tests/test_tui_app.py -q`

Expected: failures for missing policy fields and unhandled `approval-mode:` actions.

- [ ] **Step 3: Add policy fields and reducer event**

In `src/miniclaude/cli/tui/state.py`, add:

```python
    shell_enabled: bool = False
    approval_mode: str = "inline"
```

to `SessionViewState`; add matching keyword parameters to `initial_session_view`; and add this reducer branch:

```python
    elif kind == "runtime_policy":
        changes.update(
            shell_enabled=bool(event.get("shell_enabled", state.shell_enabled)),
            approval_mode=str(event.get("approval_mode", state.approval_mode)),
        )
```

- [ ] **Step 4: Apply policy actions and refresh the sidebar**

Initialize the view with CLI options in `src/miniclaude/cli/tui/app.py`:

```python
        self.view_state = initial_session_view(
            session["session_id"],
            session["workspace"],
            turns=session["turn_index"],
            shell_enabled=bool(self.workflow_options.get("allow_shell", False)),
            approval_mode=str(self.workflow_options.get("approval_mode", "inline")),
        )
```

Handle the typed result in `_run_command`:

```python
            elif result.action and result.action.startswith("approval-mode:"):
                mode = result.action.partition(":")[2]
                self.workflow_options["approval_mode"] = mode
                self.workflow_options["allow_shell"] = mode != "deny"
                self.view_state = reduce_session_event(
                    self.view_state,
                    {
                        "type": "runtime_policy",
                        "shell_enabled": mode != "deny",
                        "approval_mode": mode,
                    },
                )
                self.query_one(SessionSidebar).update_state(self.view_state)
```

Add these two lines in `SessionSidebar.update_state` in `src/miniclaude/cli/tui/widgets.py` after `route`:

```python
            f"shell       {'enabled' if state.shell_enabled else 'disabled'}",
            f"approval    {state.approval_mode}",
```

Pass the same policy fields when `action_new_session` rebuilds the view.

- [ ] **Step 5: Correct the copied TUI test source and run it**

Ensure the context-manager line in `tests/test_tui_app.py` is:

```python
        async with app.run_test() as pilot:
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tui_state.py tests/test_tui_app.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit live policy state**

```powershell
git add src/miniclaude/cli/tui/state.py src/miniclaude/cli/tui/widgets.py src/miniclaude/cli/tui/app.py tests/test_tui_state.py tests/test_tui_app.py
git commit -m "feat: apply shell policy changes in tui"
```

### Task 3: Add a reusable cancellation primitive

**Files:**
- Create: `src/miniclaude/core/cancellation.py`
- Modify: `src/miniclaude/core/state.py`
- Create: `tests/test_cancellation.py`
- Modify: `tests/test_graph_state.py`

- [ ] **Step 1: Write cancellation-token tests**

Create `tests/test_cancellation.py`:

```python
import pytest

from miniclaude.core.cancellation import CancellationToken, TurnCancelled


def test_cancel_is_idempotent_and_invokes_each_callback_once():
    token = CancellationToken()
    calls = []
    unregister = token.register(lambda: calls.append("stopped"))

    assert token.cancel("Escape pressed") is True
    assert token.cancel("Ctrl+C pressed") is False
    unregister()

    assert calls == ["stopped"]
    assert token.cancelled is True
    assert token.reason == "Escape pressed"


def test_late_registration_runs_immediately_and_checkpoint_raises():
    token = CancellationToken()
    calls = []
    token.cancel("user cancelled")

    token.register(lambda: calls.append("late"))

    assert calls == ["late"]
    with pytest.raises(TurnCancelled, match="user cancelled"):
        token.checkpoint()
```

- [ ] **Step 2: Run the new test and verify import failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cancellation.py -q`

Expected: collection fails because `miniclaude.core.cancellation` does not exist.

- [ ] **Step 3: Implement the token**

Create `src/miniclaude/core/cancellation.py`:

```python
from __future__ import annotations

import threading
from collections.abc import Callable


class TurnCancelled(RuntimeError):
    """Internal control flow for a user-cancelled turn."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_key = 0
        self._reason = "Turn cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def checkpoint(self) -> None:
        if self.cancelled:
            raise TurnCancelled(self.reason)

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if self._event.is_set():
                run_now = True
                key = -1
            else:
                run_now = False
                key = self._next_key
                self._next_key += 1
                self._callbacks[key] = callback
        if run_now:
            callback()

        def unregister() -> None:
            with self._lock:
                self._callbacks.pop(key, None)

        return unregister

    def cancel(self, reason: str = "Turn cancelled") -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = str(reason)[:500] or "Turn cancelled"
            self._event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue
        return True
```

- [ ] **Step 4: Attach it to runtime state**

In `src/miniclaude/core/state.py`, import `CancellationToken` and add:

```python
    run_id: str = ""
    cancellation: CancellationToken = field(default_factory=CancellationToken)
```

Add a graph-state test:

```python
def test_runtime_has_independent_cancellation_tokens(tmp_path):
    first = RuntimeState(tmp_path / "one")
    second = RuntimeState(tmp_path / "two")

    first.cancellation.cancel("first")

    assert first.cancellation.cancelled is True
    assert second.cancellation.cancelled is False
```

- [ ] **Step 5: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cancellation.py tests/test_graph_state.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the primitive**

```powershell
git add src/miniclaude/core/cancellation.py src/miniclaude/core/state.py tests/test_cancellation.py tests/test_graph_state.py
git commit -m "feat: add cooperative turn cancellation token"
```

### Task 4: Stop model/tool work at cancellation boundaries

**Files:**
- Modify: `src/miniclaude/core/agent.py`
- Modify: `src/miniclaude/tools/registry.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add failing model and tool-boundary tests**

Add to `tests/test_agent.py`:

```python
def test_cancelled_runtime_never_invokes_model(tmp_path):
    runtime = RuntimeState(tmp_path)
    runtime.cancellation.cancel("test cancellation")
    model = ScriptedModel([])

    events = list(stream_agent_events("build", workspace=tmp_path, runtime=runtime, model=model))

    assert events[-1] == {
        "type": "cancelled",
        "reason": "test cancellation",
    }
    assert model.inputs == []
```

Add to `tests/test_tools.py`:

```python
def test_cancelled_runtime_does_not_dispatch_file_tool(tmp_path):
    runtime = RuntimeState(tmp_path)
    runtime.cancellation.cancel("stop")
    tools = build_tools(runtime)

    result = execute_tool(
        tools,
        "FileWriteTool",
        {"file_path": "blocked.txt", "content": "must not exist"},
    )

    assert result == {"ok": False, "cancelled": True, "error": "stop"}
    assert not (tmp_path / "blocked.txt").exists()
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tools.py -q`

Expected: the cancelled model still binds or invokes, and the file is written.

- [ ] **Step 3: Guard ReAct and tool dispatch**

In `src/miniclaude/core/agent.py`, add a helper and call it before tool binding, before every model invocation, after every model invocation, and before each `execute_tool` call:

```python
def _cancel_event(state: RuntimeState) -> dict[str, object] | None:
    if not state.cancellation.cancelled:
        return None
    return {"type": "cancelled", "reason": state.cancellation.reason}
```

At each boundary, return after yielding the event:

```python
        cancelled = _cancel_event(state)
        if cancelled is not None:
            yield cancelled
            return
```

In `src/miniclaude/tools/registry.py`, import `TurnCancelled`, catch it before the current error boundary, and call `state.cancellation.checkpoint()` at the start of each nested tool function:

```python
    def write(file_path: str, content: str) -> dict:
        state.cancellation.checkpoint()
        return write_file(state, file_path, content)

    def edit(file_path: str, old_text: str, new_text: str) -> dict:
        state.cancellation.checkpoint()
        return edit_file(state, file_path, old_text, new_text)

    def bash(command: str, timeout_seconds: float | None = None) -> dict:
        state.cancellation.checkpoint()
        return run_bash(state, command, timeout_seconds)

    try:
        return tool.invoke(args)
    except TurnCancelled as exc:
        return {"ok": False, "cancelled": True, "error": str(exc)}
```

Add the same `state.cancellation.checkpoint()` first line to the existing `read` and `search` closures. The closures already own the exact `RuntimeState`, so `execute_tool` keeps its public signature stable.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_tools.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit boundary checks**

```powershell
git add src/miniclaude/core/agent.py src/miniclaude/tools/registry.py tests/test_agent.py tests/test_tools.py
git commit -m "feat: stop model and tool dispatch after cancellation"
```

### Task 5: Terminate a running Bash process tree

**Files:**
- Modify: `src/miniclaude/tools/bash_tool.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write a failing cancellable-process test**

Add to `tests/test_tools.py`:

```python
def test_shell_cancellation_is_distinct_from_timeout(tmp_path):
    runtime = RuntimeState(tmp_path, allow_shell=True, approval_mode="auto")
    result_box = {}

    def invoke():
        result_box["result"] = run_bash(
            runtime,
            'python -c "import time; time.sleep(30)"',
            timeout_seconds=60,
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    time.sleep(0.2)
    runtime.cancellation.cancel("Escape pressed")
    thread.join(timeout=8)

    assert thread.is_alive() is False
    assert result_box["result"]["ok"] is False
    assert result_box["result"]["cancelled"] is True
    assert result_box["result"]["timed_out"] is False
    assert "Escape pressed" in result_box["result"]["error"]
```

Add `import threading` to the test module.

Add this termination-failure test below it:

```python
def test_shell_cancellation_reports_unconfirmed_termination(tmp_path, monkeypatch):
    import io
    import subprocess

    class UnstoppableProcess:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("test", timeout)

        def kill(self):
            pass

    runtime = RuntimeState(tmp_path, allow_shell=True, approval_mode="auto")
    runtime.cancellation.cancel("stop")
    monkeypatch.setattr(
        "miniclaude.tools.bash_tool.subprocess.Popen",
        lambda *args, **kwargs: UnstoppableProcess(),
    )
    monkeypatch.setattr("miniclaude.tools.bash_tool._stop_tree", lambda process: None)

    result = run_bash(runtime, "python --version")

    assert result["ok"] is False
    assert result["cancelled"] is True
    assert result["termination_failed"] is True
    assert result["error"] == (
        "Cancellation requested; process-tree termination could not be confirmed"
    )
```

- [ ] **Step 2: Run the test and verify it blocks or lacks `cancelled`**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tools.py::test_shell_cancellation_is_distinct_from_timeout -q`

Expected: failure because `run_bash` only waits for completion or timeout.

- [ ] **Step 3: Poll completion, timeout, and cancellation**

In `run_bash`, replace the blocking wait with token-aware polling in the Bash worker thread:

```python
    cancelled = False
    termination_failed = False
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_tree(process)
                break
            if state.cancellation.wait(min(0.05, remaining)):
                cancelled = True
                _stop_tree(process)
                break
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            termination_failed = True
    except BaseException:
        _stop_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1)
```

Do not register `_stop_tree` as a token callback: token callbacks run in the caller of `cancel()`, while process termination must stay in the Bash worker so the TUI can recover promptly.

Import `time`. Include cancellation in result construction:

```python
        "ok": process.returncode == 0 and not timed_out and not cancelled and not incomplete,
        "cancelled": cancelled,
```

Give it precedence over timeout/exit errors:

```python
    if cancelled:
        if termination_failed or process.poll() is None:
            result["termination_failed"] = True
            result["error"] = (
                "Cancellation requested; process-tree termination could not be confirmed"
            )
        else:
            result["error"] = state.cancellation.reason
    elif timed_out:
        result["error"] = "Command timed out; process-tree termination was attempted"
```

- [ ] **Step 4: Run Shell tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tools.py -q`

Expected: all tests pass, including existing timeout and approval behavior.

- [ ] **Step 5: Commit process-tree cancellation**

```powershell
git add src/miniclaude/tools/bash_tool.py tests/test_tools.py
git commit -m "feat: cancel running shell process trees"
```

### Task 6: Persist cancelled Session turns and traces

**Files:**
- Modify: `src/miniclaude/core/session.py`
- Modify: `src/miniclaude/core/session_controller.py`
- Modify: `src/miniclaude/core/agent.py`
- Modify: `src/miniclaude/core/harness.py`
- Modify: `src/miniclaude/core/trace.py`
- Modify: `tests/test_session.py`
- Modify: `tests/test_session_controller.py`
- Modify: `tests/test_harness.py`
- Modify: `tests/test_trace.py`

- [ ] **Step 1: Write failing Session cancellation tests**

Add to `tests/test_session.py`:

```python
def test_cancelled_user_turn_persists_without_assistant(tmp_path):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "long request", run_id="run-one")

    assert mark_turn_cancelled(session, turn, "run-one", "Escape pressed") is True
    assert mark_turn_cancelled(session, turn, "run-one", "again") is False
    save_session(tmp_path, session)

    loaded = load_session(tmp_path, session["session_id"])
    assert loaded["recent_turns"][-1]["role"] == "user"
    assert loaded["recent_turns"][-1]["status"] == "cancelled"
    assert loaded["recent_turns"][-1]["run_id"] == "run-one"
    assert not any(item["role"] == "assistant" for item in loaded["recent_turns"])
```

Add to `tests/test_session_controller.py`:

```python
def test_cancelled_turn_is_not_saved_as_assistant_and_next_turn_can_start(tmp_path):
    session = create_session(tmp_path)
    token = CancellationToken()

    def cancelled_router(*args, **kwargs):
        token.cancel("Ctrl+C pressed")
        return {"route": "chat", "reason": "chat", "confidence": 1.0}

    first = list(
        stream_session_turn(
            "first",
            session=session,
            startup_directory=tmp_path,
            run_id="run-one",
            cancellation=token,
            router=cancelled_router,
            chat=lambda *args, **kwargs: pytest.fail("chat must not run"),
        )
    )
    second = list(
        stream_session_turn(
            "second",
            session=session,
            startup_directory=tmp_path,
            run_id="run-two",
            cancellation=CancellationToken(),
            router=lambda *args, **kwargs: {
                "route": "chat",
                "reason": "chat",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: "answer",
        )
    )

    assert first[-1]["type"] == "session_cancelled"
    assert second[-1]["type"] == "session_final"
    assert [item["role"] for item in session["recent_turns"]] == [
        "user",
        "user",
        "assistant",
    ]
```

- [ ] **Step 2: Run Session tests and verify signature/schema failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_session.py tests/test_session_controller.py -q`

Expected: failures for missing `run_id`, `mark_turn_cancelled`, and `cancellation` arguments.

- [ ] **Step 3: Extend the Session schema safely**

In `src/miniclaude/core/session.py`:

```python
SESSION_FORMAT_VERSION = 3


def append_user_turn(session: SessionData, content: str, *, run_id: str = "") -> int:
    if not isinstance(content, str) or not content.strip():
        raise SessionError("User turn content must not be blank")
    session["turn_index"] += 1
    turn = session["turn_index"]
    item: dict[str, object] = {
        "role": "user",
        "turn": turn,
        "content": _bounded(content),
        "created_at": _now(),
    }
    if run_id:
        item["run_id"] = _bounded(run_id, 64)
    session["recent_turns"].append(item)
    session["recent_turns"] = session["recent_turns"][-MAX_RECENT_TURNS:]
    return turn


def mark_turn_cancelled(
    session: SessionData,
    turn: int,
    run_id: str,
    reason: str,
) -> bool:
    for item in reversed(session["recent_turns"]):
        if item.get("role") == "user" and item.get("turn") == turn:
            if item.get("run_id", "") != run_id or item.get("status") == "cancelled":
                return False
            item["status"] = "cancelled"
            item["cancel_reason"] = _bounded(reason, 500)
            item["cancelled_at"] = _now()
            return True
    return False
```

Allow `run_id`, `status`, `cancel_reason`, and `cancelled_at` only on user records; validate `status == "cancelled"`, bounded strings, and timezone-aware `cancelled_at`. Upgrade both format versions 1 and 2 to version 3 during load without inventing cancellation fields.

- [ ] **Step 4: Make Session cancellation idempotent and replacement-safe**

In `src/miniclaude/core/session_controller.py`, accept:

```python
    run_id: str = "",
    cancellation: CancellationToken | None = None,
```

Use `token = cancellation or CancellationToken()` and append the user turn with `run_id`. Release the per-Session mutation lock after each append/save instead of holding it across provider calls. Before invoking `workflow_stream`, add the shared identity to its options:

```python
            options = dict(workflow_options or {})
            options["run_id"] = run_id
            options["cancellation"] = token
```

Register this callback immediately after the first Session save:

```python
    def persist_cancellation() -> None:
        with _session_lock(session["session_id"]):
            if mark_turn_cancelled(session, turn, run_id, token.reason):
                save_session(startup_directory, session)

    unregister_cancel = token.register(persist_cancellation)
```

Check `token.cancelled` after routing, after chat, during workflow iteration, and before assistant persistence. At each check, call `persist_cancellation()`, yield this event, and return:

```python
        {
            "type": "session_cancelled",
            "turn": turn,
            "run_id": run_id,
            "reason": token.reason,
        }
```

Always call `unregister_cancel()` in `finally`. Only the current non-cancelled turn may call `append_assistant_turn`; a cancelled older run cannot write after a replacement turn advances `turn_index`.

- [ ] **Step 5: Add harness and trace cancellation tests**

Add to `tests/test_harness.py`:

```python
def test_harness_cancel_is_idempotent(tmp_path):
    calls = []
    harness = HarnessRunner(
        RuntimeState(tmp_path),
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    harness.start({"task": "build", "passed": False})

    first = harness.cancel(latest_node="actor", reason="Escape pressed")
    second = harness.cancel(latest_node="actor", reason="again")

    assert first["type"] == "trace_summary"
    assert first["status"] == "cancelled"
    assert second is None
    assert calls.count("trace.end.cancelled") == 1
```

Add to `tests/test_trace.py`:

```python
def test_trace_records_cancelled_status(tmp_path):
    runtime = RuntimeState(tmp_path)
    trace = TraceRecorder(runtime, "build")
    trace.start({"task": "build"})
    trace.record_custom_event({"type": "session_cancelled", "reason": "Escape pressed"})

    summary = trace.end(status="cancelled", latest_node="actor", final_state={})

    assert summary["status"] == "cancelled"
    assert summary["cancellation_count"] == 1
```

- [ ] **Step 6: Make harness lifecycle thread-safe and pass the token into workflow runtime**

Add `cancellation_count: int = 0` to `TraceStats` and increment it for `session_cancelled` events. Add `self._lifecycle_lock = threading.RLock()` to `HarnessRunner`, guard `record_custom_event`, `record_graph_update`, and `finish`, then add:

```python
    def cancel(self, *, latest_node: str, reason: str) -> dict[str, object] | None:
        self.record_custom_event(
            {"type": "session_cancelled", "reason": str(reason)[:500]},
            self._state,
        )
        return self.finish(
            status="cancelled",
            latest_node=latest_node,
            state=self._state,
        )
```

Extend `stream_workflow_events` in `src/miniclaude/core/agent.py` with `run_id` and `cancellation` keyword arguments. Pass both to `RuntimeState`. Initialize `latest_node = "start"` before registering this callback after `harness.start(state)`:

```python
    unregister_cancel = runtime.cancellation.register(
        lambda: harness.cancel(latest_node=latest_node, reason=runtime.cancellation.reason)
    )
```

Check the token before entering `compiled.stream`, in its output loop, and before normal `harness.finish`. On cancellation, drain harness events and return without yielding a normal `final`. Unregister the callback in `finally`.

- [ ] **Step 7: Run persistence and harness tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_session.py tests/test_session_controller.py tests/test_harness.py tests/test_trace.py tests/test_agent.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit cancellation persistence**

```powershell
git add src/miniclaude/core/session.py src/miniclaude/core/session_controller.py src/miniclaude/core/agent.py src/miniclaude/core/harness.py src/miniclaude/core/trace.py tests/test_session.py tests/test_session_controller.py tests/test_harness.py tests/test_trace.py tests/test_agent.py
git commit -m "feat: persist cancelled session runs"
```

### Task 7: Make TUI cancellation immediate and stale-event safe

**Files:**
- Modify: `src/miniclaude/cli/tui/app.py`
- Modify: `src/miniclaude/cli/tui/state.py`
- Modify: `src/miniclaude/cli/tui/approval.py`
- Modify: `tests/test_tui_app.py`
- Modify: `tests/test_tui_state.py`
- Modify: `tests/test_tui_approval.py`

- [ ] **Step 1: Add failing reducer tests for the two cancellation phases**

Add to `tests/test_tui_state.py`:

```python
def test_cancellation_moves_through_cancelling_to_idle():
    state = initial_session_view("abc123def456", Path("workspace"))
    state = reduce_session_event(state, {"type": "session_status", "status": "running"})
    state = reduce_session_event(state, {"type": "session_cancelling"})
    assert state.status == "cancelling"

    state = reduce_session_event(state, {"type": "session_cancelled"})
    assert state.status == "idle"
    assert state.approval_pending is False
```

- [ ] **Step 2: Add failing TUI tests for Escape, Ctrl+C, and stale events**

Add to `tests/test_tui_app.py`:

```python
def test_escape_cancels_turn_and_immediately_recovers_prompt(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocking_stream(task, *, cancellation, run_id, **kwargs):
        started.set()
        while not cancellation.wait(0.01):
            pass
        release.wait(1)
        yield {"type": "session_status", "status": "running"}

    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=blocking_stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "long task")
            await asyncio.to_thread(started.wait, 1)
            old_run_id = app._active_run_id
            await pilot.press("escape")
            await pilot.pause()

            prompt = app.query_one("#prompt", Input)
            assert app._turn_active is False
            assert prompt.disabled is False
            assert app.view_state.status == "idle"

            app.on_agent_event_message(
                AgentEventMessage(
                    {"type": "session_status", "status": "running"},
                    run_id=old_run_id,
                )
            )
            assert app.view_state.status == "idle"
            release.set()

    asyncio.run(scenario())


def test_idle_escape_does_not_exit_but_idle_ctrl_c_exits(tmp_path):
    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test() as pilot:
            await pilot.press("escape")
            assert app.is_running is True
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.is_running is False

    asyncio.run(scenario())
```

Add `import threading` and import `AgentEventMessage` from the TUI app module.

- [ ] **Step 3: Give messages and active turns a run identity**

In `src/miniclaude/cli/tui/app.py`, import `dataclass`, `uuid4`, and `CancellationToken`; define:

```python
@dataclass
class ActiveTurn:
    run_id: str
    cancellation: CancellationToken
    worker: object | None = None


class AgentEventMessage(Message):
    def __init__(self, event: dict, *, run_id: str):
        super().__init__()
        self.event = event
        self.run_id = run_id


class TurnCompletedMessage(Message):
    def __init__(self, *, run_id: str):
        super().__init__()
        self.run_id = run_id


class ApprovalRequestedMessage(Message):
    def __init__(self, gate: ApprovalGate, *, run_id: str):
        super().__init__()
        self.gate = gate
        self.run_id = run_id
```

Remove the existing `_turn_active` and `_active_worker` assignments, then store `self._active_turn: ActiveTurn | None = None`. Keep `_turn_active` and `_active_run_id` read-only properties for existing tests:

```python
    @property
    def _turn_active(self) -> bool:
        return self._active_turn is not None

    @property
    def _active_run_id(self) -> str:
        return self._active_turn.run_id if self._active_turn else ""
```

On submit, create `ActiveTurn(uuid4().hex[:12], CancellationToken())`, start `_run_turn(task, run_id, cancellation)`, and assign its worker.

- [ ] **Step 4: Pass the run identity through the worker and filter messages**

Update `_run_turn` to place these options into the Session controller call:

```python
            events_source = self.turn_stream(
                task,
                session=self.session,
                startup_directory=self.startup_directory,
                model=self.model,
                run_id=run_id,
                cancellation=cancellation,
                workflow_options=options,
            )
```

Bind the approval handler to this run in `_run_turn`:

```python
        options["approval_handler"] = lambda request: self._approval_handler(request, run_id)
```

Change `_approval_handler` to accept `run_id`, post `ApprovalRequestedMessage(gate, run_id=run_id)`, and post its resolution as `AgentEventMessage(event, run_id=run_id)`. Post every worker event as `AgentEventMessage(event, run_id=run_id)` and completion as `TurnCompletedMessage(run_id=run_id)`.

At the start of the agent-event, turn-completed, and approval-requested handlers, ignore any message whose run ID does not match `_active_run_id`. This prevents a cancelled run from showing a late approval modal as well as preventing stale status/output changes.

- [ ] **Step 5: Implement one idempotent cancellation coordinator**

Add Escape to `BINDINGS`:

```python
        ("escape", "cancel", "Cancel"),
```

Implement:

```python
    def _finish_run(self, run_id: str) -> None:
        if self._active_turn is None or self._active_turn.run_id != run_id:
            return
        self._active_turn = None
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    def _cancel_active_turn(self, reason: str) -> bool:
        active = self._active_turn
        if active is None:
            return False
        first = active.cancellation.cancel(reason)
        if not first:
            return True
        self.view_state = reduce_session_event(
            self.view_state,
            {"type": "session_cancelling", "run_id": active.run_id},
        )
        self.query_one(SessionSidebar).update_state(self.view_state)
        self.gate_registry.deny_all(reason)
        if isinstance(self.screen, ApprovalModal):
            self.screen.dismiss(False)
        if active.worker is not None:
            active.worker.cancel()
        self.view_state = reduce_session_event(
            self.view_state,
            {"type": "session_cancelled", "run_id": active.run_id},
        )
        self.query_one(SessionSidebar).update_state(self.view_state)
        self._finish_run(active.run_id)
        return True

    def action_cancel(self) -> None:
        self._cancel_active_turn("Escape pressed")

    def action_cancel_or_quit(self) -> None:
        if self._cancel_active_turn("Ctrl+C pressed"):
            return
        self.exit()
```

Change reducer branches:

```python
    elif kind == "session_cancelling":
        changes.update(status="cancelling", approval_pending=False)
    elif kind == "session_cancelled":
        changes.update(status="idle", approval_pending=False)
```

Call `_cancel_active_turn("TUI closed")` from `on_unmount` before the final `deny_all`.

- [ ] **Step 6: Make approval cleanup idempotent**

Add a test in `tests/test_tui_approval.py` that creates a gate, calls `deny_all` twice, and asserts `wait()` returns one rejected decision. Preserve the current `resolve` lock and ensure modal dismissal cannot convert a prior denial into approval.

- [ ] **Step 7: Run all TUI tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tui_app.py tests/test_tui_state.py tests/test_tui_approval.py -q`

Expected: all tests pass; the prompt is enabled before the blocked worker emits another event.

- [ ] **Step 8: Commit TUI cancellation**

```powershell
git add src/miniclaude/cli/tui/app.py src/miniclaude/cli/tui/state.py src/miniclaude/cli/tui/approval.py tests/test_tui_app.py tests/test_tui_state.py tests/test_tui_approval.py
git commit -m "feat: cancel active tui runs with ctrl-c or escape"
```

### Task 8: Full regression and clean handoff

**Files:**
- Verify: all source and test files changed above.

- [ ] **Step 1: Verify built-in help as the current user-facing documentation**

Run the registry help directly:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from miniclaude.commands.registry import CommandContext, build_command_registry; c=CommandContext('abc123def456', Path('.').resolve(), 'idle', '(none)', False, False, 'inline'); print(build_command_registry().execute('/help', c).message)"
```

Expected: output contains `/approve [mode]` and does not instruct the user to restart Miniclaude. README work stays deferred as requested.

- [ ] **Step 2: Run the cancellation and policy test slice**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cancellation.py tests/test_commands.py tests/test_tui_state.py tests/test_tui_app.py tests/test_tui_approval.py tests/test_tools.py tests/test_agent.py tests/test_session.py tests/test_session_controller.py tests/test_harness.py tests/test_trace.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run the complete test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass; environment-dependent live tests may remain explicitly skipped.

- [ ] **Step 4: Run static checks**

Run: `.\.venv\Scripts\ruff.exe check .`

Expected: `All checks passed!`

- [ ] **Step 5: Inspect the final diff and repository status**

Run:

```powershell
git diff --check
git status --short
git log -8 --oneline
```

Expected: no whitespace errors. Preserve unrelated pre-existing edits rather than staging them accidentally.

- [ ] **Step 6: Confirm only intentional implementation files are staged**

```powershell
git diff --cached --name-only
```

Expected: no unrelated pre-existing UI or product-document files appear in the staged list.
