# Approval Mode All Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--approval-mode all` so every safe or risky shell command requires interactive approval while blocked commands remain impossible to approve.

**Architecture:** Extend the existing `RuntimeState` and CLI mode validation without adding a second policy flag. Adjust BashTool's existing approval gate so classifier-safe commands bypass approval in every mode except `all`; reuse the inline approval handler and preserve the current event schema and fail-closed behavior.

**Tech Stack:** Python 3.13, Typer, Rich, pytest, dataclasses, existing Miniclaude execution harness.

---

## File map

- Modify `src/miniclaude/core/state.py`: define and validate `all` as a runtime approval mode.
- Modify `src/miniclaude/cli/app.py`: accept `all`, advertise it in help, and install the interactive handler for it.
- Modify `src/miniclaude/tools/bash_tool.py`: require approval for safe commands only when the policy is `all`.
- Modify `tests/test_graph_state.py`: cover runtime acceptance and validation messaging.
- Modify `tests/test_cli.py`: cover CLI acceptance, workflow propagation, and handler installation.
- Modify `tests/test_tools.py`: cover approve, reject, risky, and blocked BashTool paths under `all`.
- Modify `docs/stage5.md`: document the fourth mode and give a resume example.

### Task 1: Runtime and CLI contract

**Files:**
- Modify: `tests/test_graph_state.py:45-67`
- Modify: `tests/test_cli.py:196-207,256-273`
- Modify: `src/miniclaude/core/state.py:1-38`
- Modify: `src/miniclaude/cli/app.py:73-135`

- [ ] **Step 1: Write failing runtime tests**

Add this test after `test_runtime_has_stage_five_defaults` in `tests/test_graph_state.py`:

```python
def test_runtime_accepts_all_approval_mode(tmp_path):
    runtime = RuntimeState(tmp_path / "workspace", approval_mode="all")

    assert runtime.approval_mode == "all"
```

Strengthen the approval-mode validation case so its expected message proves the public choices are
current:

```python
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("approval_mode", "unknown", "inline, all, auto, or deny"),
        ("checkpoint_mode", "sometimes", "checkpoint mode"),
        ("trace_mode", "verbose", "trace mode"),
    ],
)
```

- [ ] **Step 2: Run the runtime tests and verify RED**

Run:

```powershell
uv run pytest tests/test_graph_state.py::test_runtime_accepts_all_approval_mode tests/test_graph_state.py::test_runtime_rejects_invalid_harness_modes -q
```

Expected: `test_runtime_accepts_all_approval_mode` fails because `RuntimeState` rejects `all`, and
the validation-message case fails because the old message omits `all`.

- [ ] **Step 3: Write failing CLI test**

Add this test after `test_cli_stage_five_defaults` in `tests/test_cli.py`:

```python
def test_cli_all_approval_mode_installs_handler_and_reaches_workflow(monkeypatch, tmp_path):
    calls = install_fake_workflow_events(monkeypatch, FakeWorkflowEvents())

    result = runner.invoke(
        app,
        ["task", "--workspace", str(tmp_path), "--approval-mode", "all"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["approval_mode"] == "all"
    assert callable(calls[0]["approval_handler"])
```

- [ ] **Step 4: Run the CLI test and verify RED**

Run:

```powershell
uv run pytest tests/test_cli.py::test_cli_all_approval_mode_installs_handler_and_reaches_workflow -q
```

Expected: FAIL with `Invalid approval mode: all`.

- [ ] **Step 5: Implement the minimal runtime and CLI support**

In `src/miniclaude/core/state.py`, change the field and validation to:

```python
approval_mode: Literal["inline", "all", "auto", "deny"] = "inline"
```

```python
if self.approval_mode not in {"inline", "all", "auto", "deny"}:
    raise ValueError("approval mode must be inline, all, auto, or deny")
```

In `src/miniclaude/cli/app.py`, update the option help and allowed set:

```python
approval_mode: Annotated[
    str, typer.Option(help="inline, all, auto, or deny.")
] = "inline",
```

```python
"approval": (approval_mode, {"inline", "all", "auto", "deny"}),
```

Install the existing handler for both interactive modes:

```python
approval_handler = (
    make_inline_approval_handler(console)
    if approval_mode in {"inline", "all"}
    else None
)
```

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_graph_state.py::test_runtime_accepts_all_approval_mode tests/test_graph_state.py::test_runtime_rejects_invalid_harness_modes tests/test_cli.py::test_cli_all_approval_mode_installs_handler_and_reaches_workflow tests/test_cli.py::test_stage_five_mode_validation_happens_before_model -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the contract change**

```powershell
git add tests/test_graph_state.py tests/test_cli.py src/miniclaude/core/state.py src/miniclaude/cli/app.py
git commit -m "feat: accept approval mode all"
```

### Task 2: BashTool approve-all policy

**Files:**
- Modify: `tests/test_tools.py:280-435`
- Modify: `src/miniclaude/tools/bash_tool.py:66-112`

- [ ] **Step 1: Write failing tests for safe-command approval and rejection**

Add these tests after `test_safe_command_bypasses_approval_handler` in `tests/test_tools.py`:

```python
@pytest.mark.parametrize(
    "decision,expected_ok,expected_started",
    [
        (ApprovalDecision(True, "yes"), True, 1),
        (ApprovalDecision(False, "no"), False, 0),
    ],
)
def test_all_mode_requires_decision_for_safe_command(
    tmp_path, monkeypatch, decision, expected_ok, expected_started
):
    events = []
    requests = []
    runtime = RuntimeState(
        tmp_path,
        allow_shell=True,
        approval_mode="all",
        approval_handler=lambda request: requests.append(request) or decision,
        event_handler=events.append,
    )
    started = _recording_popen(monkeypatch)

    result = run_bash(runtime, "python --version")

    assert result["ok"] is expected_ok
    assert result["risk_level"] == "safe"
    assert result["requires_approval"] is True
    assert result["approved"] is decision.approved
    assert len(requests) == 1
    assert len(started) == expected_started
    assert [event["type"] for event in events] == [
        "approval_requested",
        "approval_resolved",
    ]
```

- [ ] **Step 2: Run the safe-command test and verify RED**

Run:

```powershell
uv run pytest tests/test_tools.py::test_all_mode_requires_decision_for_safe_command -q
```

Expected: FAIL because classifier-safe commands currently return from `_approval_result` before
creating an approval request.

- [ ] **Step 3: Write failing tests for risky and blocked commands**

Add:

```python
def test_all_mode_requires_decision_for_risky_command(tmp_path, monkeypatch):
    calls = []
    runtime = RuntimeState(
        tmp_path,
        allow_shell=True,
        approval_mode="all",
        approval_handler=lambda request: calls.append(request) or ApprovalDecision(False, "no"),
    )
    started = _recording_popen(monkeypatch)

    result = run_bash(runtime, "pip install flask")

    assert result["ok"] is False
    assert result["risk_level"] == "risky"
    assert result["requires_approval"] is True
    assert len(calls) == 1
    assert started == []


def test_all_mode_cannot_approve_blocked_command(tmp_path, monkeypatch):
    calls = []
    runtime = RuntimeState(
        tmp_path,
        allow_shell=True,
        approval_mode="all",
        approval_handler=lambda request: calls.append(request) or ApprovalDecision(True, "yes"),
    )
    started = _recording_popen(monkeypatch)

    result = run_bash(runtime, "git reset --hard")

    assert result["ok"] is False
    assert result["risk_level"] == "blocked"
    assert result["requires_approval"] is False
    assert calls == []
    assert started == []
```

- [ ] **Step 4: Run the policy tests and verify their state before implementation**

Run:

```powershell
uv run pytest tests/test_tools.py::test_all_mode_requires_decision_for_risky_command tests/test_tools.py::test_all_mode_cannot_approve_blocked_command -q
```

Expected: the risky and blocked assertions describe behavior already shared with `inline`, while
construction still fails until Task 1 allows the new mode. After Task 1 they may pass immediately;
this is acceptable because the new behavior under test is the safe-command policy proven RED in
Step 2, while these cases are explicit regression guards for the agreed safety boundary.

- [ ] **Step 5: Implement the minimal approve-all gate**

In `src/miniclaude/tools/bash_tool.py`, replace the safe-command early return and base metadata with:

```python
risk = classify_command_risk(command, workspace=state.workspace)
if risk.level == "safe" and state.approval_mode != "all":
    return None

request = make_approval_request(command, risk, state.workspace)
requires_approval = risk.level == "risky" or (
    risk.level == "safe" and state.approval_mode == "all"
)
base: dict[str, object] = {
    "requires_approval": requires_approval,
    "approval_id": request.id,
    "risk_level": risk.level,
    "risk_reason": risk.reason,
    "approved": False,
}
```

Leave the existing blocked, auto, deny, handler, event, and subprocess branches unchanged.

- [ ] **Step 6: Run approval tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_tools.py -q
```

Expected: all tool tests pass, including the existing test proving safe commands still bypass the
handler in `inline` mode.

- [ ] **Step 7: Commit the BashTool policy**

```powershell
git add tests/test_tools.py src/miniclaude/tools/bash_tool.py
git commit -m "feat: approve every shell command in all mode"
```

### Task 3: Documentation and complete verification

**Files:**
- Modify: `docs/stage5.md:8-32,60-81`

- [ ] **Step 1: Update the stage-five mode reference**

Replace the approval-mode introduction with text that retains the existing descriptions and adds:

```markdown
- `all`：安全命令和风险命令都在交互式终端显示完整审批面板并询问；默认答案为拒绝。
  blocked 命令仍然直接拒绝，不能人工放行。
```

After the ordinary resume command, add this explicit verification example:

```powershell
uv run miniclaude --resume D:\path\to\workspace --allow-shell --approval-mode all
```

Explain immediately below it that `all` produces a terminal confirmation for each safe or risky
BashTool command, still requires `--allow-shell`, and is not a graphical modal.

- [ ] **Step 2: Verify CLI help advertises the mode**

Run:

```powershell
uv run miniclaude --help
```

Expected: the `--approval-mode` help contains `inline, all, auto, or deny`.

- [ ] **Step 3: Run focused approval and CLI suites**

Run:

```powershell
uv run pytest tests/test_graph_state.py tests/test_tools.py tests/test_cli.py -q
```

Expected: all selected tests pass with no warnings introduced by this change.

- [ ] **Step 4: Run the complete offline suite**

Run:

```powershell
uv run pytest -q
```

Expected: all non-explicit live tests pass; tests requiring paid DeepSeek/Tavily authorization
remain skipped according to their existing markers/options.

- [ ] **Step 5: Check formatting and repository cleanliness**

Run:

```powershell
uv run ruff check src tests
git diff --check
git status --short
```

Expected: Ruff and `git diff --check` succeed. Git status lists only the intended documentation
change before the final documentation commit.

- [ ] **Step 6: Commit the documentation**

```powershell
git add docs/stage5.md
git commit -m "docs: explain approval mode all"
```

- [ ] **Step 7: Manually verify the interactive resume path**

Run in a real interactive terminal, replacing the workspace path with an existing resumable
workspace:

```powershell
uv run miniclaude --resume "D:\workspace\miniclaude\.miniclaude\workspaces\b95e49d72e31" --allow-shell --approval-mode all
```

Expected: every safe or risky BashTool call displays `Approval Required` and waits for an explicit
answer. Answering no returns an approval-denied tool result without starting the command. A blocked
command is rejected without offering an approval prompt.
