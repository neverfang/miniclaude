# Stage 6 TUI, Session, and Intent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Textual TUI with isolated multi-turn sessions, DeepSeek intent routing, a live Session sidebar, separated execution cards, and fail-closed modal approvals without breaking the Stage 5 CLI.

**Architecture:** Keep Session persistence and routing UI-independent, wrap the existing Stage 5 generator with a single-turn Session controller, and deliver normalized events to Textual through worker-safe messages. Reuse existing approval, checkpoint, trace, and workflow contracts; Textual is a presentation adapter rather than a second agent runtime.

**Tech Stack:** Python 3.13, Textual 8.x, Typer, Rich, LangGraph, LangChain OpenAI-compatible DeepSeek provider, Pydantic, pytest.

---

## File map

- Modify `pyproject.toml`: add Textual 8.x and bump package version to 0.6.0.
- Create `src/miniclaude/core/session.py`: versioned Session store, index, turns, bounded context.
- Create `src/miniclaude/core/session_controller.py`: one-turn chat/workflow orchestration and normalized events.
- Create `src/miniclaude/graph/entry_workflow.py`: structured intent router and tool-free chat responder.
- Create `src/miniclaude/prompts/stage6.py`: routing and chat prompts.
- Create `src/miniclaude/cli/tui/__init__.py`: TUI package export.
- Create `src/miniclaude/cli/tui/approval.py`: `ApprovalGate` and `ApprovalModal`.
- Create `src/miniclaude/cli/tui/state.py`: deterministic Session sidebar event aggregation.
- Create `src/miniclaude/cli/tui/widgets.py`: plan, event-card, conversation, and sidebar widgets.
- Create `src/miniclaude/cli/tui/app.py`: Textual application, workers, messages, shortcuts, layout.
- Create `src/miniclaude/cli/tui/app.tcss`: responsive layout and status styling.
- Modify `src/miniclaude/cli/app.py`: dispatch no-task runs to TUI and validate Session options.
- Modify `README.md`: mark Stage 6 complete and document entry commands.
- Create focused tests for every new module plus an opt-in Stage 6 live test.

### Task 1: Textual dependency and version contract

**Files:**
- Modify: `pyproject.toml:10-25`
- Modify: `tests/test_package.py`

- [ ] **Step 1: Write the failing package contract test**

Add to `tests/test_package.py`:

```python
def test_stage_six_package_metadata():
    import tomllib
    from pathlib import Path

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == "0.6.0"
    assert any(requirement.startswith("textual>=8.2") for requirement in project["dependencies"])
```

- [ ] **Step 2: Run the test and verify RED**

Run `uv run pytest tests/test_package.py::test_stage_six_package_metadata -q`.

Expected: FAIL because version is 0.5.0 and Textual is absent.

- [ ] **Step 3: Add the supported dependency range**

Update `pyproject.toml`:

```toml
version = "0.6.0"
```

```toml
  "textual>=8.2,<9",
```

Run `uv lock` to update `uv.lock` using the declared range.

- [ ] **Step 4: Verify GREEN and import compatibility**

Run:

```powershell
uv run pytest tests/test_package.py::test_stage_six_package_metadata -q
uv run python -c "import textual; print(textual.__version__)"
```

Expected: test passes and Textual reports an 8.x version.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml uv.lock tests/test_package.py
git commit -m "build: add textual for stage six"
```

### Task 2: Versioned Session persistence and selection

**Files:**
- Create: `src/miniclaude/core/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing creation, selection, and turn tests**

Create `tests/test_session.py` with tests using this public API:

```python
from miniclaude.core.session import (
    SessionError,
    append_assistant_turn,
    append_user_turn,
    create_session,
    load_latest_session,
    load_session,
    save_session,
)


def test_plain_creation_always_makes_a_new_isolated_session(tmp_path):
    first = create_session(tmp_path)
    second = create_session(tmp_path)

    assert first["session_id"] != second["session_id"]
    assert first["workspace"] != second["workspace"]
    assert first["workspace"].is_dir()
    assert second["workspace"].is_dir()


def test_save_and_load_latest_session_with_ordered_turns(tmp_path):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "你好")
    append_assistant_turn(session, turn=turn, route="chat", content="你好，我在。")
    save_session(tmp_path, session)

    loaded = load_latest_session(tmp_path)

    assert loaded["session_id"] == session["session_id"]
    assert loaded["turn_index"] == 1
    assert [item["role"] for item in loaded["recent_turns"]] == ["user", "assistant"]
    summary = (
        tmp_path
        / ".miniclaude"
        / "sessions"
        / session["session_id"]
        / "SESSION_SUMMARY.md"
    ).read_text(encoding="utf-8")
    assert "你好" in summary
    assert "chat" in summary


def test_continue_without_history_is_an_actionable_error(tmp_path):
    with pytest.raises(SessionError, match="No previous session"):
        load_latest_session(tmp_path)


def test_explicit_session_rejects_path_shaped_identifier(tmp_path):
    with pytest.raises(SessionError, match="session id"):
        load_session(tmp_path, "../outside")
```

- [ ] **Step 2: Run tests and verify RED**

Run `uv run pytest tests/test_session.py -q`.

Expected: collection fails because `miniclaude.core.session` does not exist.

- [ ] **Step 3: Implement the Session store**

Create `src/miniclaude/core/session.py` with these concrete contracts:

```python
SESSION_FORMAT_VERSION = 1
MAX_RECENT_TURNS = 20
MAX_TURN_CONTENT = 4_000
MAX_SESSION_CONTEXT = 7_000
SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")

class SessionError(ValueError):
    pass

class SessionData(TypedDict):
    format_version: int
    session_id: str
    turn_index: int
    recent_turns: list[dict[str, object]]
    created_at: str
    updated_at: str
    workspace: Path
    latest_checkpoint: str
    latest_trace_id: str

def session_root(startup_directory: Path) -> Path:
    return Path(startup_directory).resolve() / ".miniclaude" / "sessions"

def create_session(startup_directory: Path) -> SessionData:
    session_id = uuid4().hex[:12]
    workspace = session_root(startup_directory) / session_id / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC).isoformat()
    session = SessionData(
        format_version=SESSION_FORMAT_VERSION,
        session_id=session_id,
        turn_index=0,
        recent_turns=[],
        created_at=now,
        updated_at=now,
        workspace=workspace,
        latest_checkpoint="",
        latest_trace_id="",
    )
    save_session(startup_directory, session)
    return session
```

Implement `append_user_turn`, `append_assistant_turn`, `save_session`, `load_session`, and
`load_latest_session` with strict types, role/route validation, bounded contents, an atomic
temporary-file replacement, a bounded `SESSION_SUMMARY.md`, and an index that contains only Session
ID plus update timestamp. Persist the workspace as the fixed relative string `workspace`; rebuild
the absolute `Path` only after verifying the Session directory remains below `session_root`.

- [ ] **Step 4: Add corruption, atomicity, and bounds tests**

Add tests asserting:

```python
def test_invalid_session_is_not_overwritten(tmp_path):
    session = create_session(tmp_path)
    path = tmp_path / ".miniclaude" / "sessions" / session["session_id"] / "session.json"
    path.write_text('{"format_version":999}', encoding="utf-8")

    with pytest.raises(SessionError, match="version"):
        load_session(tmp_path, session["session_id"])

    assert path.read_text(encoding="utf-8") == '{"format_version":999}'


def test_turn_content_and_recent_history_are_bounded(tmp_path):
    session = create_session(tmp_path)
    for index in range(30):
        turn = append_user_turn(session, f"{index}:" + "x" * 5000)
        append_assistant_turn(session, turn=turn, route="chat", content="answer")

    assert len(session["recent_turns"]) == MAX_RECENT_TURNS
    assert all(len(str(item["content"])) <= MAX_TURN_CONTENT for item in session["recent_turns"])
```

- [ ] **Step 5: Run Session tests and verify GREEN**

Run `uv run pytest tests/test_session.py -q`.

Expected: all Session store tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/miniclaude/core/session.py tests/test_session.py
git commit -m "feat: persist isolated stage six sessions"
```

### Task 3: Bounded Session context

**Files:**
- Modify: `src/miniclaude/core/session.py`
- Modify: `tests/test_session.py`

- [ ] **Step 1: Write failing context tests**

Add:

```python
from miniclaude.core.session import MAX_SESSION_CONTEXT, build_session_context


def test_context_contains_recent_turns_and_safe_workspace_listing(tmp_path):
    session = create_session(tmp_path)
    (session["workspace"] / "app.py").write_text("secret file body", encoding="utf-8")
    (session["workspace"] / ".env").write_text("API_KEY=secret", encoding="utf-8")
    turn = append_user_turn(session, "创建应用")
    append_assistant_turn(session, turn=turn, route="workflow", content="完成", summary="创建 app.py")

    context = build_session_context(session)

    assert "app.py" in context
    assert ".env" not in context
    assert "secret file body" not in context
    assert "创建 app.py" in context


def test_context_is_bounded_and_prefers_newer_turns(tmp_path):
    session = create_session(tmp_path)
    for index in range(20):
        turn = append_user_turn(session, f"old-{index}-" + "x" * 1000)
        append_assistant_turn(session, turn=turn, route="chat", content=f"answer-{index}")

    context = build_session_context(session)

    assert len(context) <= MAX_SESSION_CONTEXT
    assert "answer-19" in context
```

- [ ] **Step 2: Run tests and verify RED**

Run `uv run pytest tests/test_session.py -q`.

Expected: FAIL because `build_session_context` is missing.

- [ ] **Step 3: Implement context construction**

Add `build_session_context(session: SessionData) -> str`. Walk only the Session workspace with
`followlinks=False`, reuse `protected_part`, collect at most 30 regular relative paths sorted by
mtime descending, select the last 10 turns, prefer `summary` over `content`, and build newest-first
conversation blocks before truncating the final string to `MAX_SESSION_CONTEXT`.

- [ ] **Step 4: Verify and commit**

Run `uv run pytest tests/test_session.py -q` and expect all tests to pass.

```powershell
git add src/miniclaude/core/session.py tests/test_session.py
git commit -m "feat: build bounded session context"
```

### Task 4: DeepSeek intent router and tool-free chat responder

**Files:**
- Create: `src/miniclaude/prompts/stage6.py`
- Create: `src/miniclaude/graph/entry_workflow.py`
- Create: `tests/test_entry_workflow.py`

- [ ] **Step 1: Write failing router tests**

Create tests around an injected structured-output model:

```python
def test_router_accepts_confident_chat():
    model = StructuredSequenceModel([{"route": "chat", "reason": "greeting", "confidence": 0.9}])
    result = route_intent("你好", session_context="", model=model)
    assert result == {"route": "chat", "reason": "greeting", "confidence": 0.9}


@pytest.mark.parametrize(
    "reply",
    [
        {"route": "chat", "reason": "uncertain", "confidence": 0.54},
        {"route": "unknown", "reason": "bad", "confidence": 1.0},
        {"route": "chat", "reason": "bad", "confidence": float("nan")},
    ],
)
def test_router_falls_back_to_workflow(reply):
    result = route_intent("继续", session_context="prior coding task", model=StructuredSequenceModel([reply]))
    assert result["route"] == "workflow"
    assert result["confidence"] == 0.0


def test_router_provider_failure_falls_back_without_secret():
    result = route_intent("hello", session_context="", model=FailingModel("api_key=secret"))
    assert result["route"] == "workflow"
    assert "secret" not in result["reason"]
```

- [ ] **Step 2: Run tests and verify RED**

Run `uv run pytest tests/test_entry_workflow.py -q`.

Expected: collection fails because the entry module does not exist.

- [ ] **Step 3: Implement prompts and typed output**

In `src/miniclaude/prompts/stage6.py`, define the exact router and chat prompts from the approved
spec: tool-requiring work and contextual continuations route to workflow; uncertainty routes to
workflow; chat may not claim tool actions.

In `entry_workflow.py`, define:

```python
class IntentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    route: Literal["chat", "workflow"]
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

def route_intent(task: str, *, session_context: str, model: StructuredOutputModel) -> dict[str, object]:
    try:
        structured = model.with_structured_output(IntentOutput, method="function_calling")
        raw = structured.invoke([SystemMessage(content=INTENT_ROUTER_PROMPT), HumanMessage(content=...)])
        decision = raw if isinstance(raw, IntentOutput) else IntentOutput.model_validate(raw)
        if decision.confidence < 0.55:
            raise ValueError("low confidence")
        return decision.model_dump()
    except Exception as exc:
        return {"route": "workflow", "reason": f"router fallback ({type(exc).__name__})", "confidence": 0.0}
```

Implement `respond_chat` with `model.invoke`, no tool binding, `_text` extraction, nonblank bounded
output, and a sanitized generic exception.

- [ ] **Step 4: Add chat no-tools test**

```python
def test_chat_responder_never_binds_tools():
    model = ChatModelSpy(AIMessage(content="你好，我在。"))
    answer = respond_chat("你好", session_context="", model=model)
    assert answer == "你好，我在。"
    assert model.bind_tools_calls == 0
```

- [ ] **Step 5: Verify and commit**

Run `uv run pytest tests/test_entry_workflow.py -q` and expect all tests to pass.

```powershell
git add src/miniclaude/prompts/stage6.py src/miniclaude/graph/entry_workflow.py tests/test_entry_workflow.py
git commit -m "feat: route session turns to chat or workflow"
```

### Task 5: Session turn controller and UI event aggregation

**Files:**
- Create: `src/miniclaude/core/session_controller.py`
- Create: `src/miniclaude/cli/tui/state.py`
- Create: `tests/test_session_controller.py`
- Create: `tests/test_tui_state.py`

- [ ] **Step 1: Write failing controller route tests**

Use injected `router`, `chat`, and `workflow_stream` functions:

```python
def test_chat_turn_does_not_start_workflow(tmp_path):
    session = create_session(tmp_path)
    workflow_calls = []
    events = list(stream_session_turn(
        "你好",
        session=session,
        startup_directory=tmp_path,
        router=lambda *args, **kwargs: {"route": "chat", "reason": "greeting", "confidence": 1.0},
        chat=lambda *args, **kwargs: "你好，我在。",
        workflow_stream=lambda *args, **kwargs: workflow_calls.append(args) or iter(()),
    ))
    assert workflow_calls == []
    assert events[-1]["type"] == "session_final"
    assert events[-1]["route"] == "chat"


def test_workflow_turn_forwards_existing_events(tmp_path):
    session = create_session(tmp_path)
    source = iter([{"type": "planner", "todos": []}, {"type": "final", "passed": True, "content": "done"}])
    events = list(stream_session_turn(
        "build",
        session=session,
        startup_directory=tmp_path,
        router=lambda *args, **kwargs: {"route": "workflow", "reason": "work", "confidence": 1.0},
        chat=lambda *args, **kwargs: pytest.fail("chat must not run"),
        workflow_stream=lambda *args, **kwargs: source,
    ))
    assert any(event["type"] == "planner" for event in events)
    assert events[-1]["type"] == "session_final"
    assert events[-1]["content"] == "done"
```

- [ ] **Step 2: Run controller tests and verify RED**

Run `uv run pytest tests/test_session_controller.py -q`.

Expected: collection fails because the controller does not exist.

- [ ] **Step 3: Implement one-turn orchestration**

Implement `stream_session_turn` to reject blank input, reject a concurrent turn with a lock,
append/save the user turn, emit `session_status=routing`, build context, call the router, emit
`intent_decision`, run either chat or the injected existing workflow stream, extract the final
answer, append/save exactly one assistant turn, and emit `session_final`. On exceptions emit a
sanitized `session_error`, save a failed summary, and always release the turn lock.

- [ ] **Step 4: Write and implement the deterministic sidebar reducer**

Define `SessionViewState` and `reduce_session_event(state, event)` in `tui/state.py`. Tests must
assert exact transitions:

```python
def test_sidebar_reducer_tracks_workflow_metrics():
    state = initial_session_view("abc123", Path("workspace"))
    for event in [
        {"type": "session_status", "status": "running"},
        {"type": "intent_decision", "route": "workflow"},
        {"type": "react_event", "event": {"type": "tool_result", "result": {"ok": False}}},
        {"type": "approval_requested"},
        {"type": "checkpoint_saved", "checkpoint_id": "cp-1"},
    ]:
        state = reduce_session_event(state, event)
    assert state.status == "waiting approval"
    assert state.route == "workflow"
    assert state.tool_calls == 1
    assert state.failed_tools == 1
    assert state.approvals == 1
    assert state.checkpoint == "cp-1"
```

- [ ] **Step 5: Verify and commit**

Run:

```powershell
uv run pytest tests/test_session_controller.py tests/test_tui_state.py -q
```

Expected: all tests pass.

```powershell
git add src/miniclaude/core/session_controller.py src/miniclaude/cli/tui/state.py tests/test_session_controller.py tests/test_tui_state.py
git commit -m "feat: orchestrate and summarize session turns"
```

### Task 6: Thread-safe TUI approval bridge

**Files:**
- Create: `src/miniclaude/cli/tui/__init__.py`
- Create: `src/miniclaude/cli/tui/approval.py`
- Create: `tests/test_tui_approval.py`

- [ ] **Step 1: Write failing gate tests**

```python
def test_gate_resolves_once_and_wait_returns_decision():
    gate = ApprovalGate(request())
    assert gate.resolve(True, "Approved in TUI") is True
    assert gate.resolve(False, "late denial") is False
    assert gate.wait(timeout=0.01).approved is True


def test_gate_timeout_fails_closed():
    gate = ApprovalGate(request())
    decision = gate.wait(timeout=0.01)
    assert decision.approved is False
    assert "timed out" in decision.reason.lower()


def test_registry_denies_every_pending_gate_on_shutdown():
    registry = ApprovalGateRegistry()
    first = registry.create(request("one"))
    second = registry.create(request("two"))
    registry.deny_all("TUI closed")
    assert first.wait(0).approved is False
    assert second.wait(0).approved is False
```

- [ ] **Step 2: Run and verify RED**

Run `uv run pytest tests/test_tui_approval.py -q`.

Expected: collection fails because approval classes do not exist.

- [ ] **Step 3: Implement gate and modal**

Implement `ApprovalGate` with `threading.Event`, a lock, idempotent `resolve`, and fail-closed
`wait`. Implement `ApprovalGateRegistry` for creation, removal, and shutdown denial.

Implement `ApprovalModal(ModalScreen[bool])` with labels for tool, level, reason, bounded workspace,
and sanitized exact command. Bind only `y` to approve; bind `n`, `escape`, and `enter` to deny.
Buttons call the same actions. `on_unmount` denies an unresolved gate.

- [ ] **Step 4: Add Textual pilot modal tests**

Test Y approval, Enter denial, Escape denial, and dismissal release using `App.run_test()` and
`pilot.press(...)`. Assert the returned `ApprovalDecision`, not merely widget visibility.

- [ ] **Step 5: Verify and commit**

Run `uv run pytest tests/test_tui_approval.py -q` and expect all tests to pass.

```powershell
git add src/miniclaude/cli/tui/__init__.py src/miniclaude/cli/tui/approval.py tests/test_tui_approval.py
git commit -m "feat: bridge shell approvals into textual"
```

### Task 7: Textual widgets, responsive layout, and worker lifecycle

**Files:**
- Create: `src/miniclaude/cli/tui/widgets.py`
- Create: `src/miniclaude/cli/tui/app.py`
- Create: `src/miniclaude/cli/tui/app.tcss`
- Create: `tests/test_tui_app.py`

- [ ] **Step 1: Write failing layout and event-card tests**

Create a fake controller stream and use `run_test()`:

```python
async def test_app_has_execution_column_and_session_sidebar(session):
    app = MiniclaudeTuiApp(session=session, turn_stream=fake_turn_stream)
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#plan")
        assert app.query_one("#event-stream")
        assert app.query_one("#conversation")
        assert app.query_one("#session-sidebar")
        assert app.query_one("#prompt")


async def test_tool_call_and_result_render_as_separate_cards(session):
    app = MiniclaudeTuiApp(session=session, turn_stream=fake_turn_stream)
    async with app.run_test() as pilot:
        await submit(pilot, "build")
        await pilot.pause()
        assert len(app.query(".tool-call-card")) == 1
        assert len(app.query(".tool-result-card")) == 1
```

- [ ] **Step 2: Run and verify RED**

Run `uv run pytest tests/test_tui_app.py -q`.

Expected: collection fails because TUI app/widgets do not exist.

- [ ] **Step 3: Implement focused widgets**

Create widgets with one responsibility each:

- `PlanPanel.update_plan(todos)` renders bounded status rows.
- `EventStream.append_event(event)` selects a card class per normalized event type.
- `ConversationPanel.append_user/append_assistant` renders only conversation content.
- `SessionSidebar.update_state(SessionViewState)` updates fixed labeled fields.

All displayed commands, outputs, summaries, and paths must pass through existing bounded sanitizer
helpers or dedicated bounded text functions.

- [ ] **Step 4: Implement the app and worker messages**

Define `AgentEventMessage`, `TurnCompletedMessage`, and `ApprovalRequestedMessage` as Textual
messages. `MiniclaudeTuiApp` uses a thread worker for `stream_session_turn`; each yielded event is
posted to the UI thread. Event handling updates the reducer, relevant widgets, and input disabled
state. No worker directly calls a widget method.

Implement bindings `ctrl+c`, `ctrl+l`, `ctrl+n`, `ctrl+o`, and `ctrl+s`. First cancellation stops
the worker and denies gates; a second cancel with no active worker exits. App shutdown always calls
`gate_registry.deny_all`.

- [ ] **Step 5: Implement responsive TCSS**

Use a two-column grid at normal width with a 32-cell sidebar. Add a narrow-screen class toggled by
`on_resize` that moves sidebar status to one line and collapses Plan. Style separate tool call,
tool result, handoff, checkpoint, trace, failure, and conversation cards without relying on color
alone.

- [ ] **Step 6: Add lifecycle and shortcut tests**

Test duplicate-submit prevention, input re-enable after final/error, sidebar reducer output,
`Ctrl+S` collapse, `Ctrl+L` visual-only clearing, one-turn cancellation, new Session switching, and
shutdown denial of a pending gate.

- [ ] **Step 7: Verify and commit**

Run `uv run pytest tests/test_tui_app.py tests/test_tui_approval.py tests/test_tui_state.py -q`.

```powershell
git add src/miniclaude/cli/tui tests/test_tui_app.py
git commit -m "feat: add stage six textual interface"
```

### Task 8: CLI dispatch, documentation, and end-to-end regression

**Files:**
- Modify: `src/miniclaude/cli/app.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_stage6_live.py`
- Modify: `tests/conftest.py`
- Modify: `README.md`
- Create: `docs/stage6.md`

- [ ] **Step 1: Write failing CLI dispatch tests**

Monkeypatch a lazy `launch_tui` function and assert:

```python
def test_no_task_launches_a_new_tui_session(monkeypatch, tmp_path):
    calls = install_fake_tui(monkeypatch)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert calls[0]["selection"] == "new"


@pytest.mark.parametrize("flag", ["-c", "--continue"])
def test_continue_launches_latest_session(monkeypatch, flag):
    calls = install_fake_tui(monkeypatch)
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert calls[0]["selection"] == "latest"


def test_explicit_session_launches_named_session(monkeypatch):
    calls = install_fake_tui(monkeypatch)
    result = runner.invoke(app, ["--session", "abc123def456"])
    assert result.exit_code == 0
    assert calls[0]["session_id"] == "abc123def456"


@pytest.mark.parametrize(
    "arguments",
    [["task", "--continue"], ["--resume", "work", "--continue"], ["--session", "abc", "--continue"]],
)
def test_session_modes_reject_ambiguous_combinations_before_model(monkeypatch, arguments):
    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: pytest.fail("no model"))
    assert runner.invoke(app, arguments).exit_code == 2
```

- [ ] **Step 2: Run and verify RED**

Run `uv run pytest tests/test_cli.py -q`.

Expected: no-task behavior and Session options fail under the Stage 5 CLI.

- [ ] **Step 3: Implement lazy TUI dispatch**

Add Typer options:

```python
continue_session: Annotated[bool, typer.Option("--continue", "-c")] = False
session_id: Annotated[str | None, typer.Option("--session")] = None
```

Validate mutually exclusive entry modes before `create_model`. When TUI mode is selected, lazily
import `launch_tui`, pass startup directory, selection, Session ID, model/policy configuration, and
return without entering the Rich one-shot loop. Preserve the existing positional task and Resume
branches byte-for-byte where possible.

- [ ] **Step 4: Add explicit live acceptance**

Add `--run-live-stage6` in `tests/conftest.py`. The live test creates a temporary Session, uses the
configured DeepSeek model, runs one chat turn and one workflow turn with shell disabled, asserts
routes and persistence, and never calls Tavily or executes risky commands.

- [ ] **Step 5: Update user documentation**

Create `docs/stage6.md` with entry commands, Session layout, router fallback, shortcuts, approval
keys, sidebar field meanings, safety limits, and live-test command. Update README Stage 6 status to
complete and change package description only if it remains accurate for all modes.

- [ ] **Step 6: Run focused and full verification**

Run:

```powershell
uv run pytest tests/test_session.py tests/test_entry_workflow.py tests/test_session_controller.py tests/test_tui_state.py tests/test_tui_approval.py tests/test_tui_app.py tests/test_cli.py -q
uv run pytest -q
uv run ruff check src tests
git diff --check
```

Expected: all offline tests pass; paid live tests remain skipped unless explicitly selected.

- [ ] **Step 7: Manually smoke-test the local TUI**

Run `uv run miniclaude`, submit a chat greeting, exit, then run `uv run miniclaude -c` and verify
the same Session ID and turn history return. Start a new Session with plain `uv run miniclaude` and
verify it receives a different ID. With `--allow-shell --approval-mode all`, submit a task that
runs `python --version`; verify the modal appears and Enter denies it.

- [ ] **Step 8: Commit**

```powershell
git add src/miniclaude/cli/app.py tests/test_cli.py tests/test_stage6_live.py tests/conftest.py README.md docs/stage6.md
git commit -m "feat: expose stage six interactive sessions"
```
