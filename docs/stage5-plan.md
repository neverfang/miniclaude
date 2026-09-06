# Stage Five Harness Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed shell approval, resumable safe-boundary checkpoints, isolated workspace snapshots, and sanitized execution traces around the Stage 4 workflow.

**Architecture:** Keep the Stage 4 LangGraph topology unchanged. Extend `RuntimeState` with Harness policy, apply approval immediately inside BashTool, and coordinate Checkpoint and Trace persistence from a focused Harness layer around the existing event stream. Resume reconstructs supported graph state and starts a new Supervisor round; workspace restoration remains a separate explicit operation.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, subprocess/Git CLI, JSON/JSONL, LangChain Core/OpenAI, LangGraph 1.x, Rich, Typer, pytest, Ruff.

---

**Repository constraints:** Work directly in the current checkout. Do not modify README. Do not read,
print, or commit `.env`. Default tests must not call DeepSeek, Tavily, package managers, download
tools, or real destructive commands. Do not run `--run-live-stage5` without separate user
authorization. Preserve Stage 1-4 builders and tests.

## File Map

- Create `src/miniclaude/core/approval.py`: risk parsing and approval data contracts.
- Create `src/miniclaude/core/sanitize.py`: shared recursive persistence sanitizer.
- Create `src/miniclaude/core/trace.py`: append-only trace recorder and summaries.
- Create `src/miniclaude/core/checkpoint.py`: checkpoint metadata, manifest, snapshot, and resume.
- Create `src/miniclaude/core/harness.py`: lifecycle coordination and warning isolation.
- Modify `src/miniclaude/core/state.py`: Stage 5 runtime policy fields and runtime-event hook.
- Modify `src/miniclaude/tools/bash_tool.py`: approval and hard-block gate.
- Modify `src/miniclaude/core/agent.py`: Stage 5 Harness integration and resume inputs.
- Modify `src/miniclaude/cli/app.py`: new CLI options and inline approval.
- Modify `src/miniclaude/cli/render.py`: new Harness panels.
- Modify `src/miniclaude/__init__.py`, `pyproject.toml`: version `0.5.0` and Stage 5 metadata.
- Create `docs/stage5.md`: learning, operation, recovery, and trace guide.
- Create focused Stage 5 test modules and extend existing core/CLI tests.

## Task 1: Stage 5 package and runtime contracts

**Files:**
- Modify: `src/miniclaude/core/state.py`
- Modify: `src/miniclaude/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_graph_state.py`
- Modify: `tests/test_package.py`

- [ ] **Step 1: Write failing runtime and package tests**

Add tests that establish independent, normalized Stage 5 defaults:

```python
def test_runtime_has_stage_five_defaults(tmp_path):
    runtime = RuntimeState(tmp_path)

    assert runtime.approval_mode == "inline"
    assert runtime.approval_handler is None
    assert runtime.checkpoint_mode == "light"
    assert runtime.trace_mode == "on"
    assert runtime.trace_id is None
    assert runtime.event_handler is None


@pytest.mark.parametrize("field,value", [
    ("approval_mode", "unknown"),
    ("checkpoint_mode", "sometimes"),
    ("trace_mode", "verbose"),
])
def test_runtime_rejects_invalid_harness_modes(tmp_path, field, value):
    with pytest.raises(ValueError, match=field.replace("_", " ")):
        RuntimeState(tmp_path, **{field: value})


def test_package_version_is_stage_five():
    assert miniclaude.__version__ == "0.5.0"
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```powershell
uv run --locked pytest tests/test_graph_state.py tests/test_package.py -q --tb=short
```

Expected: constructor-field and version assertions fail because Stage 5 contracts do not exist.

- [ ] **Step 3: Add the RuntimeState fields and validation**

Use forward-safe callable types so `state.py` does not import the approval module:

```python
from collections.abc import Callable
from typing import Any, Literal

ApprovalMode = Literal["inline", "auto", "deny"]
CheckpointMode = Literal["light", "strict", "off"]
TraceMode = Literal["on", "off"]


@dataclass
class RuntimeState:
    workspace: Path
    allow_shell: bool = False
    max_file_bytes: int = 1_048_576
    max_output_chars: int = 12_000
    command_timeout: float = 30.0
    read_snapshots: dict[Path, str] = field(default_factory=dict)
    approval_mode: ApprovalMode = "inline"
    approval_handler: Callable[[Any], Any] | None = None
    checkpoint_mode: CheckpointMode = "light"
    trace_mode: TraceMode = "on"
    trace_id: str | None = None
    event_handler: Callable[[dict[str, object]], None] | None = None

    def __post_init__(self):
        self.workspace = Path(self.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        if self.approval_mode not in {"inline", "auto", "deny"}:
            raise ValueError("approval mode must be inline, auto, or deny")
        if self.checkpoint_mode not in {"light", "strict", "off"}:
            raise ValueError("checkpoint mode must be light, strict, or off")
        if self.trace_mode not in {"on", "off"}:
            raise ValueError("trace mode must be on or off")
```

Set both package versions to `0.5.0` and update the project description to mention Harness safety
and tracing without changing dependencies.

- [ ] **Step 4: Run focused and regression tests**

Run:

```powershell
uv run --locked pytest tests/test_graph_state.py tests/test_package.py tests/test_tools.py -q
```

Expected: all selected tests pass; the symlink permission test may remain skipped on Windows.

- [ ] **Step 5: Commit the contracts**

```powershell
git add src/miniclaude/core/state.py src/miniclaude/__init__.py pyproject.toml tests/test_graph_state.py tests/test_package.py
git commit -m "feat: define stage five runtime contracts"
```

## Task 2: Shared sanitizer and command risk classifier

**Files:**
- Create: `src/miniclaude/core/sanitize.py`
- Create: `src/miniclaude/core/approval.py`
- Create: `tests/test_sanitize.py`
- Create: `tests/test_approval.py`

- [ ] **Step 1: Write sanitizer tests**

Cover recursive secret fields, inline credentials, paths, bounds, cycles, and unknown objects:

```python
def test_sanitize_redacts_fields_and_inline_secrets():
    payload = {
        "api_key": "sk-secret-value",
        "nested": {"Authorization": "Bearer abc.def.ghi"},
        "command": "curl -H 'Authorization: Bearer top-secret' https://example.test",
        "safe": "pytest -q",
    }
    clean = sanitize_for_persistence(payload)
    rendered = json.dumps(clean)
    assert "secret-value" not in rendered
    assert "abc.def.ghi" not in rendered
    assert "top-secret" not in rendered
    assert clean["api_key"] == "[REDACTED]"
    assert clean["safe"] == "pytest -q"


def test_sanitize_bounds_unknown_objects_and_cycles():
    value = []
    value.append(value)
    clean = sanitize_for_persistence(
        {"long": "x" * 20_000, "cycle": value, "unknown": object()}
    )
    assert len(clean["long"]) <= MAX_PERSISTED_STRING
    assert clean["cycle"][0] == "[CYCLE]"
    assert clean["unknown"] == "[object]"
```

- [ ] **Step 2: Write classifier tests without executing commands**

```python
@pytest.mark.parametrize("command", [
    "pytest -q",
    "python --version",
    "Get-ChildItem",
])
def test_safe_commands(command):
    assert classify_command_risk(command).level == "safe"


@pytest.mark.parametrize("command,reason", [
    ("pip install flask", "Python package installation"),
    ("python -m pip install flask", "Python package installation"),
    ("pytest -q; uv add flask", "Project dependency change"),
    ("echo ok && npm install", "Node package installation"),
    ("curl https://example.test/file", "Network download"),
    ("python -m http.server 8000", "Long-running development server"),
])
def test_risky_commands_and_compound_segments(command, reason):
    risk = classify_command_risk(command)
    assert risk.level == "risky"
    assert reason in risk.reason


@pytest.mark.parametrize("command", [
    "git reset --hard",
    "git clean -fdx",
    "Remove-Item -Recurse -Force C:\\\\",
    "rm -rf /",
    "shutdown /s /t 0",
])
def test_blocked_commands(command):
    assert classify_command_risk(command).level == "blocked"
```

Also test mixed-case spelling, newlines, pipes, quoted benign text, NUL, the 16,000-character
boundary, and suspicious ambiguous segments. Assertions must inspect only classification and must
never pass these strings to a real shell.

- [ ] **Step 3: Run tests and confirm missing-module failures**

Run:

```powershell
uv run --locked pytest tests/test_sanitize.py tests/test_approval.py -q --tb=short
```

Expected: import failures for the two new modules.

- [ ] **Step 4: Implement the bounded persistence sanitizer**

Define stable constants and a recursive public function:

```python
MAX_PERSISTED_DEPTH = 8
MAX_PERSISTED_ITEMS = 100
MAX_PERSISTED_STRING = 8_000
REDACTED = "[REDACTED]"


def sanitize_for_persistence(value: object) -> object:
    return _sanitize(value, depth=0, seen=set(), field_name="")
```

`_sanitize` must preserve `None`, booleans, finite numbers, strings, datetimes, and workspace-relative
path strings. It must copy mappings and sequences, cap collection counts, detect object identity
cycles, redact field names matching `key|token|secret|password|authorization|credential|cookie`,
apply compiled inline patterns, and return `[TypeName]` for unknown objects without calling `repr`.

- [ ] **Step 5: Implement approval contracts and classification**

Use immutable types and ordered pattern rules:

```python
RiskLevel = Literal["safe", "risky", "blocked"]


@dataclass(frozen=True)
class CommandRisk:
    level: RiskLevel
    reason: str
    segment: str = ""


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    command: str
    risk_level: RiskLevel
    risk_reason: str
    workspace: Path
    tool_name: str = "BashTool"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""


def make_approval_request(command: str, risk: CommandRisk, workspace: Path) -> ApprovalRequest:
    return ApprovalRequest(
        id=f"approval-{uuid4().hex[:8]}",
        command=command,
        risk_level=risk.level,
        risk_reason=risk.reason,
        workspace=workspace,
    )
```

Implement `_split_shell_segments` as a bounded quote-aware scanner for newline, semicolon, pipe,
`&&`, and `||`. Check blocked rules before risky rules and return `safe` only when every segment is
safe. Reject NUL and oversized commands as blocked malformed input.

- [ ] **Step 6: Run tests, Ruff, and commit**

```powershell
uv run --locked pytest tests/test_sanitize.py tests/test_approval.py -q
uv run --locked ruff check src/miniclaude/core/sanitize.py src/miniclaude/core/approval.py tests/test_sanitize.py tests/test_approval.py
git add src/miniclaude/core/sanitize.py src/miniclaude/core/approval.py tests/test_sanitize.py tests/test_approval.py
git commit -m "feat: classify shell risk and sanitize harness data"
```

Expected: focused tests and Ruff pass.

## Task 3: Enforce approval in BashTool

**Files:**
- Modify: `src/miniclaude/tools/bash_tool.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing policy tests with a mocked process boundary**

Monkeypatch `subprocess.Popen` so risky and blocked test commands never execute:

```python
def test_risky_inline_command_requires_handler(tmp_path, monkeypatch):
    runtime = RuntimeState(tmp_path, allow_shell=True, approval_mode="inline")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))

    result = run_bash(runtime, "pip install flask")

    assert result["ok"] is False
    assert result["requires_approval"] is True
    assert result["approved"] is False
    assert started == []


def test_auto_approves_risky_but_never_blocked(tmp_path, monkeypatch):
    runtime = RuntimeState(tmp_path, allow_shell=True, approval_mode="auto")
    blocked = run_bash(runtime, "git reset --hard")
    assert blocked["ok"] is False
    assert blocked["risk_level"] == "blocked"
```

Add separate tests for approve/reject decisions, handler exceptions, invalid return types, deny mode,
safe commands bypassing the handler, and exact runtime event order.

- [ ] **Step 2: Run the policy tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_tools.py -q --tb=short
```

Expected: new approval metadata and non-execution assertions fail.

- [ ] **Step 3: Add fail-closed policy resolution**

Implement helpers before process creation:

```python
def _emit_runtime_event(state: RuntimeState, event: dict[str, object]) -> None:
    if state.event_handler is not None:
        try:
            state.event_handler(event)
        except Exception:
            pass


def _approval_result(state: RuntimeState, command: str) -> dict[str, object] | None:
    risk = classify_command_risk(command)
    if risk.level == "safe":
        return None
    request = make_approval_request(command, risk, state.workspace)
    base = {
        "requires_approval": risk.level == "risky",
        "approval_id": request.id,
        "risk_level": risk.level,
        "risk_reason": risk.reason,
        "approved": False,
    }
    if risk.level == "blocked":
        return {**base, "ok": False, "error": f"blocked command: {risk.reason}"}
    if state.approval_mode == "auto":
        return {**base, "approved": True}
    if state.approval_mode == "deny" or state.approval_handler is None:
        return {**base, "ok": False, "error": f"approval denied: {risk.reason}"}
    _emit_runtime_event(state, {"type": "approval_requested", **base, "command": command})
    try:
        decision = state.approval_handler(request)
        approved = isinstance(decision, ApprovalDecision) and decision.approved
    except Exception:
        approved = False
    resolved = {**base, "approved": approved}
    _emit_runtime_event(state, {"type": "approval_resolved", **resolved})
    if not approved:
        return {**resolved, "ok": False, "error": f"approval denied: {risk.reason}"}
    return resolved
```

Call this after existing input/timeout validation and before building `argv` or starting a process.
Merge approved metadata into the normal subprocess result. Emit a resolved audit event for auto and
deny modes as well, but never call the handler for safe or blocked commands.

- [ ] **Step 4: Run BashTool regression and full approval tests**

```powershell
uv run --locked pytest tests/test_approval.py tests/test_tools.py -q
```

Expected: approval coverage and existing real safe-command tests pass.

- [ ] **Step 5: Commit BashTool approval**

```powershell
git add src/miniclaude/tools/bash_tool.py tests/test_tools.py
git commit -m "feat: gate risky shell commands with approval"
```

## Task 4: Build sanitized Trace recording

**Files:**
- Create: `src/miniclaude/core/trace.py`
- Create: `tests/test_trace.py`

- [ ] **Step 1: Write failing Trace lifecycle tests**

Use an injected clock and ID so outputs are deterministic:

```python
def test_trace_records_order_stats_and_summary(tmp_path):
    runtime = RuntimeState(tmp_path, trace_mode="on", trace_id="trace-fixed")
    recorder = TraceRecorder(runtime, task="build app", clock=FakeClock())

    recorder.start({"task": "build app"})
    recorder.record_custom_event({"type": "handoff", "to_agent": "codeAgent"})
    recorder.record_custom_event({"type": "tool_call", "name": "BashTool"})
    recorder.record_custom_event({"type": "tool_result", "name": "BashTool", "result": {"ok": False}})
    summary = recorder.end(status="failed", latest_node="verifier", final_state={"passed": False})

    events = [json.loads(line) for line in recorder.events_path.read_text().splitlines()]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert summary["tool_calls"] == 1
    assert summary["failed_tool_calls"] == 1
    assert summary["handoff_count"] == 1
    assert (recorder.root / "trace.json").exists()
    assert (recorder.root / "timeline.md").exists()
```

Add tests for approval/checkpoint/compression counters, graph node visits, resume linkage, off mode,
secret redaction, field/event bounds, literal Markdown, write failure, and immutable prior traces.

- [ ] **Step 2: Run tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_trace.py -q --tb=short
```

Expected: import failure for `TraceRecorder`.

- [ ] **Step 3: Implement TraceRecorder**

Use dependency injection for time and UUID generation:

```python
class TraceRecorder:
    def __init__(self, runtime, task="", *, clock=time.monotonic, now=None):
        self.enabled = runtime.trace_mode == "on"
        self.trace_id = runtime.trace_id or uuid4().hex[:12]
        self.root = runtime.workspace / ".miniclaude" / "traces" / self.trace_id
        self.events_path = self.root / "events.jsonl"
        self.sequence = 0
        self.timeline: list[str] = []
        self.stats = TraceStats()
```

`_append` must sanitize first, encode one compact UTF-8 JSON object, append exactly one line, flush,
and update only the relevant bounded timeline entry. `record_graph_update` accepts a mapping of node
name to update and increments each node once. `end` appends `run_end`, atomically writes
`trace.json` and `timeline.md`, and returns a serializable summary event payload.

The recorder must disable further writes after the first filesystem failure and expose one
sanitized warning through `pop_warning()` so logging cannot recursively fail.

- [ ] **Step 4: Run focused tests and lint**

```powershell
uv run --locked pytest tests/test_sanitize.py tests/test_trace.py -q
uv run --locked ruff check src/miniclaude/core/trace.py tests/test_trace.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 5: Commit Trace recording**

```powershell
git add src/miniclaude/core/trace.py tests/test_trace.py
git commit -m "feat: record sanitized execution traces"
```

## Task 5: Persist light and strict checkpoints

**Files:**
- Create: `src/miniclaude/core/checkpoint.py`
- Create: `tests/test_checkpoint.py`

- [ ] **Step 1: Write failing mode and atomic-write tests**

```python
def test_light_checkpoint_writes_resumable_metadata(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build app")
    event = manager.save(sample_graph_state(runtime), status="running", latest_node="supervisor")

    payload = json.loads((manager.root / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["format_version"] == CHECKPOINT_FORMAT_VERSION
    assert payload["status"] == "running"
    assert payload["latest_node"] == "supervisor"
    assert payload["resume_node"] == "contextual_supervisor"
    assert event["type"] == "checkpoint_saved"
    assert (manager.root / "RECOVERY.md").exists()
    assert not (manager.root / "state.json").exists()


def test_strict_checkpoint_adds_state_and_events(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="strict")
    manager = CheckpointManager(runtime, task="build app")
    manager.save(sample_graph_state(runtime), event={"type": "tool_result"})
    assert (manager.root / "state.json").exists()
    assert (manager.root / "events.jsonl").exists()


def test_checkpoint_off_writes_nothing(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="off")
    manager = CheckpointManager(runtime, task="build app")
    assert manager.save(sample_graph_state(runtime)) is None
    assert not manager.root.exists()
```

Add tests for temporary replacement cleanup, manifest exclusions/bounds, serialization of LangChain
messages, unsupported object sanitization, recovery Markdown, and simulated write failures.

- [ ] **Step 2: Run tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_checkpoint.py -q --tb=short
```

Expected: import failure for `CheckpointManager`.

- [ ] **Step 3: Implement constants, safe serialization, and atomic writers**

```python
CHECKPOINT_FORMAT_VERSION = 1
CHECKPOINT_FILE = "checkpoint.json"
STATE_FILE = "state.json"
EVENTS_FILE = "events.jsonl"
RECOVERY_FILE = "RECOVERY.md"
MAX_MANIFEST_FILES = 5_000
MAX_SNAPSHOT_FILE_BYTES = 5 * 1024 * 1024


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
```

Serialize supported message objects to `{type, content, id, name, tool_call_id, tool_calls}` with
the shared sanitizer. Reconstruct only the supported Human/System/AI/Tool message types. Runtime is
always rebuilt from CLI options and is never serialized.

- [ ] **Step 4: Implement bounded workspace manifest and CheckpointManager.save**

Walk without following links. Skip names using `protected_part`, `.miniclaude`, `.git`, venvs,
caches, and dependency directories. Record relative path, byte size, modification time, and SHA-256
for eligible regular files. Mark exclusions by aggregate reason rather than leaking protected names.

`save` constructs this versioned shape:

```python
payload = {
    "format_version": CHECKPOINT_FORMAT_VERSION,
    "checkpoint_id": checkpoint_id,
    "workspace_id": workspace_identity(self.workspace),
    "task": self.task,
    "status": status,
    "latest_node": latest_node,
    "resume_node": "contextual_supervisor",
    "saved_at": iso_timestamp(),
    "trace_id": runtime.trace_id,
    "snapshot_commit": snapshot.commit,
    "snapshot_restorable": snapshot.restorable,
    "state": serialize_resume_state(state),
    "manifest": manifest,
}
```

Strict mode writes the broader sanitized state and appends the optional event. Recovery Markdown
contains the task, status, last safe node, attempt count, snapshot status, drift warning, and exact
resume commands, all rendered literally.

- [ ] **Step 5: Run focused tests and commit storage**

```powershell
uv run --locked pytest tests/test_checkpoint.py -q
uv run --locked ruff check src/miniclaude/core/checkpoint.py tests/test_checkpoint.py
git add src/miniclaude/core/checkpoint.py tests/test_checkpoint.py
git commit -m "feat: persist resumable harness checkpoints"
```

## Task 6: Add isolated Git snapshots and explicit restore

**Files:**
- Modify: `src/miniclaude/core/checkpoint.py`
- Modify: `tests/test_checkpoint.py`

- [ ] **Step 1: Write failing snapshot isolation tests**

Use temporary workspaces and a fake Git runner for unit coverage, plus one local-Git integration
test guarded by `shutil.which("git")`:

```python
def test_snapshot_uses_separate_git_dir(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('v1')\n", encoding="utf-8")
    runtime = RuntimeState(workspace, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")

    saved = manager.save(sample_graph_state(runtime))

    assert saved["snapshot_restorable"] is True
    assert (manager.root / "snapshot.git" / "HEAD").exists()
    assert not (workspace / ".git").exists()


def test_resume_does_not_overwrite_drift_without_restore(tmp_path):
    manager, runtime = saved_checkpoint(tmp_path, content="v1")
    target = runtime.workspace / "app.py"
    target.write_text("manual v2", encoding="utf-8")

    inputs, event = manager.load_resume_inputs(runtime)

    assert target.read_text(encoding="utf-8") == "manual v2"
    assert event["workspace_drift"] is True
    assert inputs["task"] == "build"
```

Add tests for no-change commit reuse, secret/link exclusion, existing user repository isolation,
explicit restore, pre-restore backup, invalid commit/path rejection, restore failure, and preserving
untracked/protected files.

- [ ] **Step 2: Run snapshot tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_checkpoint.py -q --tb=short
```

Expected: snapshot/restore assertions fail because Git support is absent.

- [ ] **Step 3: Implement argument-array Git operations**

Centralize Git invocation without a shell:

```python
def _run_snapshot_git(self, *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        "git",
        f"--git-dir={self.snapshot_git_dir}",
        f"--work-tree={self.workspace}",
        *args,
    ]
    return subprocess.run(
        command,
        cwd=self.workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
```

Initialize a bare-style repository at `snapshot.git`, configure only its local user identity, write
an explicit exclude file, stage eligible manifest paths with `--pathspec-from-file`, and commit only
when the tree differs from HEAD. Persist sanitized error categories, never raw Git stderr.

- [ ] **Step 4: Implement guarded restoration**

`restore_workspace(commit)` performs this order:

1. validate checkpoint identity, exact hexadecimal commit, and membership in the snapshot repository;
2. snapshot the current eligible workspace and retain the backup commit;
3. list target-tree paths and validate every relative path with the workspace boundary rules;
4. extract target blobs to same-directory temporary files;
5. atomically replace only validated tracked files;
6. preserve protected and untracked files;
7. return `restore_completed` with target and backup commit IDs.

Do not use `git reset --hard`, `git clean`, or a broad recursive delete. If any extraction or replace
fails, stop and report the backup commit and affected relative path category.

- [ ] **Step 5: Run checkpoint tests and commit snapshots**

```powershell
uv run --locked pytest tests/test_checkpoint.py -q
uv run --locked ruff check src/miniclaude/core/checkpoint.py tests/test_checkpoint.py
git add src/miniclaude/core/checkpoint.py tests/test_checkpoint.py
git commit -m "feat: snapshot and restore checkpoint workspaces"
```

## Task 7: Coordinate lifecycle through HarnessRunner

**Files:**
- Create: `src/miniclaude/core/harness.py`
- Create: `tests/test_harness.py`
- Modify: `src/miniclaude/core/agent.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write failing Harness unit tests**

Use fake checkpoint and trace recorders with ordered call logs:

```python
def test_harness_orders_start_updates_and_finish(tmp_path):
    calls = []
    harness = HarnessRunner(
        RuntimeState(tmp_path),
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    state = {"task": "build", "passed": False}

    assert harness.start(state)[0]["type"] == "trace_started"
    harness.record_graph_update("supervisor", {"todos": []}, state)
    summary = harness.finish(status="finished", latest_node="final", state=state)

    assert calls == [
        "trace.start",
        "checkpoint.started",
        "trace.node.supervisor",
        "checkpoint.supervisor",
        "checkpoint.finished",
        "trace.end.finished",
    ]
    assert summary["type"] == "trace_summary"
```

Add failure-isolation tests, runtime approval event handling, warning deduplication, custom-event
checkpoint boundaries, interruption, and failure finalization.

- [ ] **Step 2: Write failing agent integration tests**

Extend the fake workflow used by `tests/test_agent.py` and inject a fake Harness factory. Assert:

- Stage 4 normalized event order is unchanged;
- Harness events appear at start and end;
- every raw graph update reaches Harness exactly once;
- custom tool/handoff/context events reach Trace exactly once;
- final checkpoint status is `passed` or `failed`, not merely `finished`;
- a generator closed by `KeyboardInterrupt` calls `interrupt` before re-raising.

- [ ] **Step 3: Run the tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_harness.py tests/test_agent.py -q --tb=short
```

Expected: Harness imports and new stream parameters fail.

- [ ] **Step 4: Implement HarnessRunner**

Keep the class focused on lifecycle:

```python
class HarnessRunner:
    def start(self, state: dict) -> list[dict]:
        self.trace.start(state)
        saved = self.checkpoint.save(state, status="started", latest_node="start")
        return [] if saved is None else [saved]

    def record_runtime_event(self, event: dict) -> None:
        self.trace.record_custom_event(event)
        self.runtime_events.append(dict(event))

    def record_custom_event(self, event: dict, state: dict) -> list[dict]:
        self.trace.record_custom_event(event)
        if event.get("type") not in CHECKPOINT_CUSTOM_EVENT_TYPES:
            return []
        saved = self.checkpoint.save(state, status="running", event=event)
        return [] if saved is None else [saved]

    def record_graph_update(self, node: str, update: dict, state: dict) -> list[dict]:
        self.trace.record_graph_update({node: update})
        saved = self.checkpoint.save(state, status="running", latest_node=node)
        return [] if saved is None else [saved]

    def finish(self, *, status: str, latest_node: str, state: dict) -> dict | None:
        saved = self.checkpoint.save(state, status=status, latest_node=latest_node)
        if saved is not None:
            self.trace.record_custom_event(saved)
        summary = self.trace.end(status=status, latest_node=latest_node, final_state=state)
        return None if summary is None else {"type": "trace_summary", **summary}

    def interrupt(self, *, latest_node: str, state: dict) -> list[dict]:
        summary = self.finish(status="interrupted", latest_node=latest_node, state=state)
        return [] if summary is None else [summary]

    def fail(self, *, latest_node: str, state: dict) -> list[dict]:
        summary = self.finish(status="failed", latest_node=latest_node, state=state)
        return [] if summary is None else [summary]
```

Every method returns stable application events and catches storage errors independently. Attach
`runtime.event_handler` to a method that immediately records approval events and queues their
normalized forms for the CLI. `drain_runtime_events()` empties that queue exactly once.

- [ ] **Step 5: Integrate Harness with `stream_workflow_events`**

Add keyword parameters:

```python
approval_mode: str = "inline"
approval_handler=None
checkpoint_mode: str = "light"
trace_mode: str = "on"
resume_workspace: Path | None = None
restore_workspace: bool = False
harness_factory=HarnessRunner
```

Build RuntimeState once, create Harness before graph construction, and choose either
`initial_graph_state` or `CheckpointManager.load_resume_inputs`. Maintain `current_state` by applying
each raw node update; preserve reducer-sensitive message fields from the graph update rather than
serializing the compiled graph object. After every custom/update item, drain runtime events and emit
Harness events. Use `try/except KeyboardInterrupt/except BaseException/finally` so finalization runs
exactly once.

Do not change `stream_agent_events` defaults for Stage 1. Existing callers that inject a runtime
continue using that runtime's policy.

- [ ] **Step 6: Run core integration and commit**

```powershell
uv run --locked pytest tests/test_harness.py tests/test_agent.py tests/test_stage4_workflow.py -q
uv run --locked ruff check src/miniclaude/core/harness.py src/miniclaude/core/agent.py tests/test_harness.py tests/test_agent.py
git add src/miniclaude/core/harness.py src/miniclaude/core/agent.py tests/test_harness.py tests/test_agent.py
git commit -m "feat: wrap stage four with harness lifecycle"
```

## Task 8: Add semantic resume validation

**Files:**
- Modify: `src/miniclaude/core/checkpoint.py`
- Modify: `src/miniclaude/core/agent.py`
- Modify: `tests/test_checkpoint.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write failing resume reconstruction tests**

```python
def test_resume_rebuilds_runtime_and_supported_graph_state(tmp_path):
    manager, old_runtime = saved_checkpoint(tmp_path, content="v1")
    new_runtime = RuntimeState(
        old_runtime.workspace,
        allow_shell=True,
        approval_mode="deny",
        checkpoint_mode="light",
        trace_mode="on",
    )

    inputs, event = manager.load_resume_inputs(new_runtime)

    assert inputs["runtime"] is new_runtime
    assert inputs["task"] == "build"
    assert inputs["attempts"] == 1
    assert inputs["context_next_node"] == "verifier"
    assert event["type"] == "resume_loaded"
    assert event["resume_node"] == "contextual_supervisor"
```

Add tests for preserving todos, sources, handoffs, compression data, messages, maximum attempts,
last error, and independent collections. Add rejection tests for invalid JSON/schema/version,
foreign workspace identity, attempts outside limits, unsupported message types, and mismatched task.

- [ ] **Step 2: Run resume tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_checkpoint.py tests/test_agent.py -q --tb=short
```

Expected: resume validation/reconstruction assertions fail.

- [ ] **Step 3: Implement strict checkpoint parsing and state reconstruction**

Create a Pydantic-free deterministic validator or small dataclass parser that validates every
required scalar/list/mapping and applies the same bounds used on save. Start from
`initial_graph_state(stored_task, runtime=new_runtime, max_attempts=stored_max_attempts)`, then copy
only allowlisted persisted fields into fresh collections.

Reset transient routing to:

```python
inputs["context_next_node"] = "verifier"
inputs["context_should_compress"] = False
inputs["context_error"] = ""
inputs["passed"] = False
inputs["final_answer"] = ""
```

The Stage 4 graph starts at `contextual_supervisor`; it does not replay the interrupted tool call.
Reject task mismatch after whitespace normalization. Return manifest drift counts without embedding
file content.

- [ ] **Step 4: Run resume and Stage 4 regressions**

```powershell
uv run --locked pytest tests/test_checkpoint.py tests/test_agent.py tests/test_stage4_workflow.py tests/test_context_compressor.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit semantic resume**

```powershell
git add src/miniclaude/core/checkpoint.py src/miniclaude/core/agent.py tests/test_checkpoint.py tests/test_agent.py
git commit -m "feat: resume workflows from safe checkpoints"
```

## Task 9: Expose Stage 5 CLI and Rich panels

**Files:**
- Modify: `src/miniclaude/cli/app.py`
- Modify: `src/miniclaude/cli/render.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_render.py`

- [ ] **Step 1: Write failing CLI contract tests**

Use `CliRunner` and monkeypatch `create_model`/`stream_workflow_events`:

```python
def test_cli_stage_five_defaults(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli_app, "create_model", lambda **kwargs: object())
    monkeypatch.setattr(cli_app, "stream_workflow_events", fake_stream(captured))

    result = runner.invoke(app, ["task", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert captured["approval_mode"] == "inline"
    assert captured["checkpoint_mode"] == "light"
    assert captured["trace_mode"] == "on"


def test_restore_workspace_requires_resume(tmp_path):
    result = runner.invoke(app, ["task", "--restore-workspace"])
    assert result.exit_code == 2
    assert "--resume" in result.output
```

Add tests for optional task during resume, mismatched workspace/resume paths, mode validation, inline
non-TTY denial, interrupt exit 130, resume errors exit 2, and final failure exit 1.

- [ ] **Step 2: Write failing renderer tests**

Render every new event to a recording console and assert literal, bounded content:

```python
@pytest.mark.parametrize("event,title", [
    ({"type": "approval_requested", "risk_reason": "Network download", "command": "curl x"}, "Approval Required"),
    ({"type": "approval_resolved", "approved": False, "risk_reason": "Network download"}, "Approval Denied"),
    ({"type": "checkpoint_saved", "status": "running", "latest_node": "supervisor"}, "Checkpoint Saved"),
    ({"type": "resume_loaded", "latest_node": "supervisor", "workspace_drift": False}, "Resume Loaded"),
    ({"type": "trace_summary", "trace_id": "abc", "status": "passed"}, "Trace Summary"),
])
def test_stage_five_panels(event, title):
    output = render_to_text(event)
    assert title in output
```

- [ ] **Step 3: Run CLI tests and confirm the red state**

```powershell
uv run --locked pytest tests/test_cli.py tests/test_cli_render.py -q --tb=short
```

Expected: missing options and unsupported-event assertions fail.

- [ ] **Step 4: Implement CLI options and inline handler**

Make `task` optional only at the Typer boundary:

```python
task: Annotated[str | None, typer.Argument(help="Task; omit when using --resume.")] = None
approval_mode: Annotated[str, typer.Option(help="inline, auto, or deny.")] = "inline"
checkpoint_mode: Annotated[str, typer.Option(help="light, strict, or off.")] = "light"
trace_mode: Annotated[str, typer.Option(help="on or off.")] = "on"
resume: Annotated[Path | None, typer.Option(help="Resume checkpoint workspace.")] = None
restore_workspace: Annotated[bool, typer.Option(help="Restore checkpoint files before resume.")] = False
```

Validate combinations before model creation. Resume implies its workspace and rejects a conflicting
`--workspace`. A new run still requires a nonblank task. Implement `make_inline_approval_handler`
with `Confirm.ask(default=False)` only when the console/input is interactive. It first calls
`render_event` with `approval_requested`, returns an `ApprovalDecision`, then renders
`approval_resolved`. Never echo a command after sanitizer flags credential-like content; display a
redacted command instead.

- [ ] **Step 5: Implement Stage 5 renderers**

Add focused functions and register them in `EVENT_RENDERERS`:

```python
"approval_requested": _approval_requested,
"approval_resolved": _approval_resolved,
"checkpoint_saved": _checkpoint_saved,
"checkpoint_warning": _harness_warning,
"resume_loaded": _resume_loaded,
"resume_warning": _harness_warning,
"trace_started": _trace_started,
"trace_summary": _trace_summary,
"trace_warning": _harness_warning,
```

Use `_bounded` and `Text` for every untrusted value. Panels show relative artifact paths, counts,
status, and latest node, not full serialized state.

- [ ] **Step 6: Run CLI tests, help smoke, and commit**

```powershell
uv run --locked pytest tests/test_cli.py tests/test_cli_render.py -q
uv run --locked miniclaude --help
uv run --locked ruff check src/miniclaude/cli tests/test_cli.py tests/test_cli_render.py
git add src/miniclaude/cli/app.py src/miniclaude/cli/render.py tests/test_cli.py tests/test_cli_render.py
git commit -m "feat: expose stage five harness controls"
```

Expected: focused tests pass and help lists all five Stage 5 options.

## Task 10: Offline interruption-to-resume acceptance

**Files:**
- Create: `tests/test_stage5_workflow.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write an offline end-to-end Harness test**

Use a deterministic fake Stage 4 workflow. The first stream writes one file update, emits approval
metadata, then raises `KeyboardInterrupt`; the second stream consumes reconstructed resume inputs and
emits a passing verifier/final update.

```python
def test_stage_five_interrupt_resume_and_trace(tmp_path):
    first = InterruptingWorkflow(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        list(stream_workflow_events(
            "build app",
            workspace=tmp_path,
            model=object(),
            workflow=first,
            approval_mode="deny",
            checkpoint_mode="strict",
            trace_mode="on",
        ))

    checkpoint = json.loads((tmp_path / ".miniclaude/checkpoints/checkpoint.json").read_text())
    assert checkpoint["status"] == "interrupted"

    events = list(stream_workflow_events(
        "build app",
        workspace=tmp_path,
        resume_workspace=tmp_path,
        model=object(),
        workflow=PassingResumeWorkflow(),
        approval_mode="deny",
        checkpoint_mode="strict",
        trace_mode="on",
    ))
    assert any(event["type"] == "resume_loaded" for event in events)
    assert next(event for event in events if event["type"] == "final")["passed"] is True
    assert len(list((tmp_path / ".miniclaude/traces").iterdir())) == 2
```

Also assert that manual workspace edits made between runs survive normal resume, and add a separate
test proving explicit restore returns the checkpoint version while preserving an untracked file.

- [ ] **Step 2: Run the test and confirm the red state if any integration is missing**

```powershell
uv run --locked pytest tests/test_stage5_workflow.py -q --tb=short
```

Expected before final integration fixes: a precise event, state, or artifact assertion fails.

- [ ] **Step 3: Make the minimal integration corrections**

Restrict corrections to Harness event ordering, checkpoint state merging, resume linkage, and
finalization. Do not change Stage 4 routing or weaken assertions. Ensure the final trace ends only
after the final checkpoint event has been recorded.

- [ ] **Step 4: Run Stage 4 and Stage 5 integration suites**

```powershell
uv run --locked pytest tests/test_stage4_workflow.py tests/test_context_compressor.py tests/test_stage5_workflow.py tests/test_harness.py -q
```

Expected: all selected tests pass offline.

- [ ] **Step 5: Commit offline acceptance**

```powershell
git add tests/test_stage5_workflow.py tests/conftest.py src/miniclaude/core src/miniclaude/tools src/miniclaude/cli
git commit -m "test: cover stage five interruption and resume"
```

## Task 11: Stage 5 learning guide and opt-in live acceptance

**Files:**
- Create: `docs/stage5.md`
- Create: `tests/test_stage5_live.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add the opt-in marker and skip contract**

Add `--run-live-stage5` in `pytest_addoption` and skip the live module unless the flag is present:

```python
def pytest_addoption(parser):
    parser.addoption(
        "--run-live-stage5",
        action="store_true",
        default=False,
        help="run paid DeepSeek Stage 5 acceptance with controlled generated execution",
    )
```

The live test must use `tmp_path`, `approval_mode="auto"`, `checkpoint_mode="strict"`, and
`trace_mode="on"`. It may run safe Python verification commands but must not install packages,
download files, start servers, use Tavily, open a GUI, or leave a background process. Use a
test-injected classifier rule to exercise an approved risky path without performing a real risky
operation.

- [ ] **Step 2: Write the Stage 5 guide**

Document:

- the Harness boundary and why it is outside the Stage 4 graph;
- `--allow-shell` versus approval mode;
- safe/risky/blocked examples;
- light/strict/off trade-offs and artifact layout;
- semantic resume and why it restarts at Supervisor;
- normal resume versus explicit workspace restoration;
- trace files, statistics, redaction, and debugging workflow;
- exit codes and recovery from corrupt checkpoints;
- exact safe demo commands for inline approval, interruption, resume, and trace inspection;
- the live-test cost/safety warning and explicit authorization requirement.

Use `miniclaude` consistently; do not reintroduce the former project name.

- [ ] **Step 3: Run docs/package tests and default skip verification**

```powershell
uv run --locked pytest tests/test_package.py tests/test_stage5_live.py -q
```

Expected: package tests pass and the live test is skipped without the flag.

- [ ] **Step 4: Commit documentation and live harness**

```powershell
git add docs/stage5.md tests/test_stage5_live.py tests/conftest.py
git commit -m "docs: add stage five harness guide"
```

Do not execute the live test in this task. It requires a separate user request after offline Stage 5
completion.

## Task 12: Final verification and design conformance

**Files:**
- Review: `docs/stage5-design.md`
- Review: `docs/stage5-plan.md`
- Review: `项目篇规划.md`
- Review: all changed Stage 5 files

- [ ] **Step 1: Run the complete offline suite**

```powershell
uv run --locked pytest -q
```

Expected: all offline tests pass; live tests and the Windows symlink permission test may be reported
as skipped for their documented reasons.

- [ ] **Step 2: Run lint and format verification**

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Expected: no lint errors and every Python file already formatted.

- [ ] **Step 3: Run CLI and package smoke tests**

```powershell
uv run --locked miniclaude --help
uv run --locked python -c "import miniclaude; print(miniclaude.__version__)"
```

Expected: help names approval/checkpoint/trace/resume/restore options and version prints `0.5.0`.

- [ ] **Step 4: Check security and repository hygiene**

```powershell
rg -n "mokioclaw|MOKIO_|sk-[A-Za-z0-9]|Bearer [A-Za-z0-9]" src tests docs pyproject.toml
git diff --check
git status --short
```

Expected: no old project name or real credential strings, no whitespace errors, and only intended
Stage 5 changes before the final commit. Test fixtures must use unmistakably fake short values.

- [ ] **Step 5: Audit every Stage 5 acceptance criterion**

Confirm with code and test evidence that:

- Shell authorization remains outermost and blocked commands never execute.
- All three approval modes are fail-closed and traceable.
- Checkpoint modes produce exactly their documented artifacts.
- Normal resume preserves current files; explicit restoration creates a backup first.
- Resume restarts at a Supervisor safe boundary.
- Trace event order, statistics, bounds, and redaction are verified.
- Harness storage failures remain visible without creating false success.
- Stage 4 context behavior and earlier stages remain intact.
- Default verification performs no external calls or risky operations.

- [ ] **Step 6: Commit any final focused corrections**

If Step 1-5 required changes, stage only those named files and use:

```powershell
git commit -m "fix: align stage five harness with design"
```

If no tracked changes remain, do not create an empty commit.
