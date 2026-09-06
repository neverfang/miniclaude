import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from miniclaude.core.checkpoint import (
    CHECKPOINT_FORMAT_VERSION,
    CheckpointManager,
    _atomic_write_text,
    serialize_resume_state,
    workspace_manifest,
)
from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import initial_graph_state


def _state(runtime):
    state = initial_graph_state("build app", runtime=runtime, max_attempts=3)
    state.update(
        {
            "plan_summary": "Build then test",
            "todos": [
                {
                    "id": "code",
                    "content": "Write code",
                    "status": "in_progress",
                    "note": "",
                }
            ],
            "acceptance_criteria": ["app exists"],
            "attempts": 1,
            "messages": [HumanMessage(content="build"), AIMessage(content="working")],
        }
    )
    return state


def test_light_checkpoint_writes_resumable_metadata(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build app")

    event = manager.save(_state(runtime), status="running", latest_node="supervisor")

    payload = json.loads((manager.root / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["format_version"] == CHECKPOINT_FORMAT_VERSION
    assert payload["status"] == "running"
    assert payload["latest_node"] == "supervisor"
    assert payload["resume_node"] == "contextual_supervisor"
    assert payload["state"]["task"] == "build app"
    assert payload["state"]["attempts"] == 1
    assert event["type"] == "checkpoint_saved"
    assert event["status"] == "running"
    assert event["latest_node"] == "supervisor"
    assert (manager.root / "RECOVERY.md").exists()
    assert not (manager.root / "state.json").exists()
    assert not (manager.root / "events.jsonl").exists()


def test_strict_checkpoint_adds_state_and_events(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="strict")
    manager = CheckpointManager(runtime, task="build app")

    manager.save(
        _state(runtime),
        status="running",
        latest_node="codeAgent",
        event={"type": "tool_result", "result": {"ok": True}},
    )

    state = json.loads((manager.root / "state.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (manager.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert state["task"] == "build app"
    assert "runtime" not in state
    assert events == [{"type": "tool_result", "result": {"ok": True}}]


def test_checkpoint_off_writes_nothing(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="off")
    manager = CheckpointManager(runtime, task="build app")

    assert manager.save(_state(runtime)) is None
    assert not manager.root.exists()


def test_resume_state_serializes_supported_messages_and_independent_collections(tmp_path):
    runtime = RuntimeState(tmp_path)
    state = _state(runtime)

    serialized = serialize_resume_state(state)

    assert serialized["messages"] == [
        {"type": "human", "content": "build", "id": None},
        {"type": "ai", "content": "working", "id": None, "tool_calls": []},
    ]
    serialized["todos"][0]["content"] = "changed"
    assert state["todos"][0]["content"] == "Write code"


def test_manifest_is_bounded_and_excludes_protected_content(tmp_path):
    runtime = RuntimeState(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=hidden", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("private", encoding="utf-8")
    (tmp_path / ".miniclaude").mkdir()
    (tmp_path / ".miniclaude/internal").write_text("internal", encoding="utf-8")

    manifest = workspace_manifest(runtime.workspace)

    assert [entry["path"] for entry in manifest["files"]] == ["src/app.py"]
    assert manifest["files"][0]["sha256"]
    rendered = json.dumps(manifest)
    assert "SECRET=hidden" not in rendered
    assert ".env" not in rendered
    assert ".git/config" not in rendered


def test_atomic_write_removes_temporary_file_after_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "checkpoint.json"

    def fail_replace(source, destination):
        raise OSError("disk failure")

    monkeypatch.setattr("miniclaude.core.checkpoint.os.replace", fail_replace)

    with pytest.raises(OSError, match="disk failure"):
        _atomic_write_text(target, "{}\n")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_recovery_guide_contains_safe_resume_commands(tmp_path):
    runtime = RuntimeState(tmp_path)
    manager = CheckpointManager(runtime, task="build app")

    manager.save(_state(runtime), status="interrupted", latest_node="codeAgent")
    guide = (manager.root / "RECOVERY.md").read_text(encoding="utf-8")

    assert "interrupted" in guide
    assert "codeAgent" in guide
    assert "miniclaude --resume" in guide
    assert "--restore-workspace" in guide
