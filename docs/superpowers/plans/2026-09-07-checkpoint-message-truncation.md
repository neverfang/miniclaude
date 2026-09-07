# Checkpoint Message Truncation Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make checkpoints with more than 100 messages resumable while retaining strict validation and compatibility with the existing legacy checkpoint.

**Architecture:** Bound typed message records before the generic persistence sanitizer so new checkpoints contain only mappings. At load time, recognize only the exact legacy truncation marker in the final list position, remove it, and pass every remaining item through the existing strict message validator.

**Tech Stack:** Python 3.13, LangChain message types, pytest, existing Miniclaude checkpoint and sanitizer modules.

---

## File map

- Modify `src/miniclaude/core/checkpoint.py`: cap new typed message lists and accept the one legacy trailing marker.
- Modify `tests/test_checkpoint.py`: prove the new serialized shape, old-checkpoint compatibility, and strict rejection of misplaced markers.

### Task 1: Produce structurally valid bounded message lists

**Files:**
- Modify: `tests/test_checkpoint.py:110-122`
- Modify: `src/miniclaude/core/checkpoint.py:15,111-129`

- [ ] **Step 1: Write the failing serialization test**

Import the shared limit in `tests/test_checkpoint.py`:

```python
from miniclaude.core.sanitize import MAX_PERSISTED_ITEMS
```

Add after `test_resume_state_serializes_supported_messages_and_independent_collections`:

```python
def test_resume_state_caps_messages_without_truncation_marker(tmp_path):
    runtime = RuntimeState(tmp_path)
    state = _state(runtime)
    state["messages"] = [
        HumanMessage(content=f"message-{index}")
        for index in range(MAX_PERSISTED_ITEMS + 1)
    ]

    serialized = serialize_resume_state(state)

    assert len(serialized["messages"]) == MAX_PERSISTED_ITEMS
    assert all(isinstance(record, dict) for record in serialized["messages"])
    assert serialized["messages"][-1]["content"] == "message-99"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
uv run pytest tests/test_checkpoint.py::test_resume_state_caps_messages_without_truncation_marker -q
```

Expected: FAIL because the current sanitizer returns 101 items and the last item is
`[TRUNCATED_ITEMS]`.

- [ ] **Step 3: Implement message-specific bounding before sanitization**

Change the sanitizer import in `src/miniclaude/core/checkpoint.py` to:

```python
from miniclaude.core.sanitize import MAX_PERSISTED_ITEMS, sanitize_for_persistence
```

Replace the message branch in `serialize_resume_state` with:

```python
if field == "messages":
    messages = value if isinstance(value, list | tuple) else []
    records = [
        _serialize_message(message)
        for message in messages
        if isinstance(message, BaseMessage)
    ]
    serialized[field] = records[:MAX_PERSISTED_ITEMS]
else:
    serialized[field] = sanitize_for_persistence(value)
```

- [ ] **Step 4: Run focused serialization tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_checkpoint.py::test_resume_state_serializes_supported_messages_and_independent_collections tests/test_checkpoint.py::test_resume_state_caps_messages_without_truncation_marker tests/test_sanitize.py -q
```

Expected: all selected tests pass, including the generic sanitizer test that still expects its
marker for ordinary sequences.

- [ ] **Step 5: Commit the save-side fix**

```powershell
git add tests/test_checkpoint.py src/miniclaude/core/checkpoint.py
git commit -m "fix: bound checkpoint message records before sanitizing"
```

### Task 2: Load legacy truncated checkpoints safely

**Files:**
- Modify: `tests/test_checkpoint.py:227-304`
- Modify: `src/miniclaude/core/checkpoint.py:543-546`

- [ ] **Step 1: Write the failing legacy compatibility test**

Add after `test_resume_rebuilds_runtime_and_supported_graph_state`:

```python
def test_resume_accepts_legacy_trailing_message_truncation_marker(tmp_path):
    manager, runtime = _saved_resume_checkpoint(tmp_path)
    path = manager.root / "checkpoint.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["messages"].append("[TRUNCATED_ITEMS]")
    path.write_text(json.dumps(payload), encoding="utf-8")

    inputs, _ = manager.load_resume_inputs(runtime)

    assert [message.type for message in inputs["messages"]] == ["human", "ai"]
```

- [ ] **Step 2: Run the compatibility test and verify RED**

Run:

```powershell
uv run pytest tests/test_checkpoint.py::test_resume_accepts_legacy_trailing_message_truncation_marker -q
```

Expected: FAIL with `Invalid checkpoint message record`.

- [ ] **Step 3: Write the strict-position regression test**

Add:

```python
def test_resume_rejects_nonfinal_message_truncation_marker(tmp_path):
    manager, runtime = _saved_resume_checkpoint(tmp_path)
    path = manager.root / "checkpoint.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["messages"].insert(0, "[TRUNCATED_ITEMS]")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="message record"):
        manager.load_resume_inputs(runtime)
```

Run:

```powershell
uv run pytest tests/test_checkpoint.py::test_resume_rejects_nonfinal_message_truncation_marker -q
```

Expected: PASS under the current strict loader; this locks the safety boundary before adding the
single-position compatibility exception.

- [ ] **Step 4: Implement the narrow legacy exception**

Replace the message-loading block with:

```python
messages = stored_state.get("messages", [])
if not isinstance(messages, list):
    raise ValueError("Invalid checkpoint messages")
messages = list(messages)
if messages and messages[-1] == "[TRUNCATED_ITEMS]":
    messages.pop()
inputs["messages"] = [self._resume_message(record) for record in messages]
```

- [ ] **Step 5: Run checkpoint tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_checkpoint.py -q
```

Expected: all checkpoint tests pass, including unsupported-message and non-final-marker rejection.

- [ ] **Step 6: Verify the user's existing checkpoint loads**

Run:

```powershell
uv run python -c "from pathlib import Path; from miniclaude.core.state import RuntimeState; from miniclaude.core.checkpoint import CheckpointManager; w=Path(r'D:\workspace\miniclaude\.miniclaude\workspaces\b95e49d72e31'); r=RuntimeState(w, allow_shell=True, approval_mode='all'); s,e=CheckpointManager(r).load_resume_inputs(r); print(len(s['messages']), e['resume_node'])"
```

Expected: exit code 0 and output ending in `contextual_supervisor`; the checkpoint file itself is
not rewritten.

- [ ] **Step 7: Run complete quality verification**

Run:

```powershell
uv run pytest -q
uv run ruff check src tests
git diff --check
```

Expected: all offline tests and Ruff pass. Paid live tests remain skipped according to existing
options.

- [ ] **Step 8: Commit the compatibility fix**

```powershell
git add tests/test_checkpoint.py src/miniclaude/core/checkpoint.py
git commit -m "fix: resume legacy truncated checkpoints"
```
