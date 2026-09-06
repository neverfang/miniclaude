import json
import shutil
import subprocess

import pytest

from miniclaude.core.checkpoint import CheckpointManager
from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import initial_graph_state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")


def _state(runtime):
    return initial_graph_state("build", runtime=runtime)


def _git(workspace, *args):
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def test_snapshot_uses_separate_git_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('v1')\n", encoding="utf-8")
    runtime = RuntimeState(workspace, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")

    event = manager.save(_state(runtime))

    assert event["snapshot_restorable"] is True
    assert len(event["snapshot_commit"]) == 40
    assert (manager.root / "snapshot.git" / "HEAD").exists()
    assert not (workspace / ".git").exists()
    payload = json.loads((manager.root / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["snapshot_commit"] == event["snapshot_commit"]
    assert payload["snapshot_restorable"] is True


def test_unchanged_snapshot_reuses_commit_and_change_creates_commit(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")
    (tmp_path / "app.py").write_text("v1", encoding="utf-8")

    first = manager.save(_state(runtime))["snapshot_commit"]
    second = manager.save(_state(runtime))["snapshot_commit"]
    (tmp_path / "app.py").write_text("v2", encoding="utf-8")
    third = manager.save(_state(runtime))["snapshot_commit"]

    assert first == second
    assert third != second


def test_snapshot_does_not_modify_existing_user_repository(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "user@example.invalid")
    _git(tmp_path, "config", "user.name", "User")
    (tmp_path / "owned.txt").write_text("user", encoding="utf-8")
    _git(tmp_path, "add", "owned.txt")
    _git(tmp_path, "commit", "-m", "user commit")
    original_head = _git(tmp_path, "rev-parse", "HEAD")
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")

    CheckpointManager(runtime, task="build").save(_state(runtime))

    assert _git(tmp_path, "rev-parse", "HEAD") == original_head
    assert _git(tmp_path, "status", "--short") == "?? .miniclaude/"


def test_explicit_restore_creates_backup_and_preserves_untracked_files(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")
    target = tmp_path / "app.py"
    target.write_text("v1", encoding="utf-8")
    saved = manager.save(_state(runtime))
    target.write_text("manual v2", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("keep", encoding="utf-8")

    restored = manager.restore_workspace(saved["snapshot_commit"])

    assert restored["type"] == "restore_completed"
    assert restored["target_commit"] == saved["snapshot_commit"]
    assert restored["backup_commit"] != saved["snapshot_commit"]
    assert target.read_text(encoding="utf-8") == "v1"
    assert (tmp_path / "untracked.txt").read_text(encoding="utf-8") == "keep"


def test_restore_rejects_unknown_commit_without_mutation(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")
    target = tmp_path / "app.py"
    target.write_text("v1", encoding="utf-8")
    manager.save(_state(runtime))
    target.write_text("manual", encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot commit"):
        manager.restore_workspace("f" * 40)

    assert target.read_text(encoding="utf-8") == "manual"


def test_snapshot_excludes_protected_files(tmp_path):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")
    (tmp_path / "app.py").write_text("safe", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")

    saved = manager.save(_state(runtime))
    listing = subprocess.run(
        [
            "git",
            f"--git-dir={manager.snapshot_git_dir}",
            "ls-tree",
            "-r",
            "--name-only",
            saved["snapshot_commit"],
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout

    assert "app.py" in listing
    assert ".env" not in listing


def test_snapshot_failure_keeps_checkpoint_but_marks_it_nonrestorable(tmp_path, monkeypatch):
    runtime = RuntimeState(tmp_path, checkpoint_mode="light")
    manager = CheckpointManager(runtime, task="build")
    (tmp_path / "app.py").write_text("safe", encoding="utf-8")

    def fail_git(*args, **kwargs):
        raise OSError("private git detail")

    monkeypatch.setattr(manager, "_run_git", fail_git)

    event = manager.save(_state(runtime))

    assert event["snapshot_restorable"] is False
    assert event["snapshot_commit"] == ""
    assert event["snapshot_error"] == "Workspace snapshot failed (OSError)"
    assert (manager.root / "checkpoint.json").exists()
