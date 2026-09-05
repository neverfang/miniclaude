import importlib

import pytest
from typer.testing import CliRunner

from miniclaude.cli.app import app

cli_module = importlib.import_module("miniclaude.cli.app")
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_THINKING"):
        monkeypatch.delenv(key, raising=False)


def test_help_and_missing_task():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--workspace" in result.output
    assert "--allow-shell" in result.output
    assert "--max-attempts" in result.output
    assert runner.invoke(app, []).exit_code != 0


def test_missing_config_does_not_create_workspace(tmp_path):
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
    assert not (tmp_path / ".miniclaude").exists()


@pytest.mark.parametrize(
    "arguments",
    [["task", "--max-loops", "0"], ["task", "--max-attempts", "0"], ["   "]],
)
def test_invalid_input(arguments):
    assert runner.invoke(app, arguments).exit_code == 2


class OneAnswer:
    pass


class FakeWorkflowEvents:
    def __init__(self, *, passed=True, error=None):
        self.passed = passed
        self.error = error
        self.calls = []

    def stream(self, task, **kwargs):
        self.calls.append((task, kwargs))
        kwargs["workspace"].mkdir(parents=True, exist_ok=True)
        if self.error:
            raise self.error
        yield {
            "type": "planner",
            "attempt": 1,
            "plan_summary": "Write tests, then code",
            "todos": [
                {"id": "tests", "content": "Write tests", "status": "in_progress", "note": ""}
            ],
            "acceptance_criteria": ["tests pass"],
            "verification_commands": ["pytest -q"],
        }
        yield {
            "type": "react_event",
            "role": "actor",
            "event": {
                "type": "tool_call",
                "name": "FileWriteTool",
                "args": {"file_path": "hello.py", "content": "print('hello')"},
                "id": "write-1",
            },
        }
        yield {
            "type": "react_event",
            "role": "actor",
            "event": {
                "type": "tool_result",
                "name": "FileWriteTool",
                "id": "write-1",
                "result": {"ok": True, "path": "hello.py"},
            },
        }
        yield {"type": "actor", "attempt": 1, "summary": "implemented", "ok": True, "todos": []}
        yield {
            "type": "verifier",
            "attempt": 1,
            "passed": self.passed,
            "reason": "checks pass" if self.passed else "tests fail",
            "checks": [{"name": "tests pass", "passed": self.passed, "detail": "evidence"}],
            "results": [
                {
                    "command": "pytest -q",
                    "ok": self.passed,
                    "exit_code": 0 if self.passed else 1,
                    "stdout": "",
                    "stderr": "",
                }
            ],
        }
        answer = (
            "Done [literal], not markup."
            if self.passed
            else "Task was not verified after 1 attempt(s)."
        )
        yield {"type": "final", "passed": self.passed, "content": answer}


def install_fake_workflow_events(monkeypatch, workflow_events):
    calls = []

    def stream_workflow_events(task, **kwargs):
        calls.append(kwargs)
        return workflow_events.stream(task, **kwargs)

    monkeypatch.setattr(cli_module, "stream_workflow_events", stream_workflow_events)
    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: OneAnswer())
    return calls


def test_cli_real_loop_and_default_workspace(monkeypatch, tmp_path):
    workflow_events = FakeWorkflowEvents()
    calls = install_fake_workflow_events(monkeypatch, workflow_events)
    result = runner.invoke(app, ["say hello", "--max-attempts", "2"])
    assert result.exit_code == 0, result.output
    assert "Done [literal], not markup." in result.output
    assert "[planner] - Attempt 1" in result.output
    assert "Tool Call - FileWriteTool" in result.output
    assert "Tool Result - FileWriteTool" in result.output
    assert result.output.index("Tool Call") < result.output.index("Tool Result")
    assert "[actor] - Attempt 1" in result.output
    assert "[verifier] - Attempt 1 - PASSED" in result.output
    assert "[final] - SUCCESS" in result.output
    assert "Acceptance criteria" in result.output
    assert "Verification commands" in result.output
    assert "Checks" in result.output
    assert len(list((tmp_path / ".miniclaude/workspaces").iterdir())) == 1
    assert calls[0]["max_attempts"] == 2
    assert calls[0]["model"] is not None
    assert "env_file" in calls[0]


def test_explicit_workspace_and_shell_warning(monkeypatch, tmp_path):
    install_fake_workflow_events(monkeypatch, FakeWorkflowEvents())
    result = runner.invoke(app, ["task", "-w", str(tmp_path / "chosen"), "--allow-shell"])
    assert result.exit_code == 0
    assert "NOT a sandbox" in result.output
    assert (tmp_path / "chosen").is_dir()


def test_model_failure_exit_code_and_no_secret(monkeypatch):
    install_fake_workflow_events(
        monkeypatch, FakeWorkflowEvents(error=RuntimeError("secret-value"))
    )
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 1
    assert "secret-value" not in result.output


def test_interrupt_exit_code(monkeypatch):
    install_fake_workflow_events(monkeypatch, FakeWorkflowEvents(error=KeyboardInterrupt()))
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 130


def test_failed_verification_has_failure_exit_code(monkeypatch):
    install_fake_workflow_events(monkeypatch, FakeWorkflowEvents(passed=False))

    result = runner.invoke(app, ["task"])

    assert result.exit_code == 1
    assert "not verified" in result.output


def test_workflow_setup_failure_is_safe(monkeypatch):
    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: OneAnswer())

    def broken_workflow(*args, **kwargs):
        raise RuntimeError("secret setup detail")

    monkeypatch.setattr(cli_module, "stream_workflow_events", broken_workflow)

    result = runner.invoke(app, ["task"])

    assert result.exit_code == 1
    assert "Run failed (RuntimeError)" in result.output
    assert "secret setup detail" not in result.output
