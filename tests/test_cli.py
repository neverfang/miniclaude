import importlib

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from miniclaude.cli.app import app

cli_module = importlib.import_module("miniclaude.cli.app")
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def test_help_and_missing_task():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--workspace" in result.output
    assert "--allow-shell" in result.output
    assert runner.invoke(app, []).exit_code != 0


def test_missing_config_does_not_create_workspace(tmp_path):
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
    assert not (tmp_path / ".miniclaude").exists()


@pytest.mark.parametrize("arguments", [["task", "--max-loops", "0"], ["   "]])
def test_invalid_input(arguments):
    assert runner.invoke(app, arguments).exit_code == 2


class OneAnswer:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Done [literal], not markup.")


def test_cli_real_loop_and_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: OneAnswer())
    result = runner.invoke(app, ["say hello"])
    assert result.exit_code == 0, result.output
    assert "Done [literal], not markup." in result.output
    assert len(list((tmp_path / ".miniclaude/workspaces").iterdir())) == 1


def test_explicit_workspace_and_shell_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: OneAnswer())
    result = runner.invoke(app, ["task", "-w", str(tmp_path / "chosen"), "--allow-shell"])
    assert result.exit_code == 0
    assert "NOT a sandbox" in result.output
    assert (tmp_path / "chosen").is_dir()


def test_model_failure_exit_code_and_no_secret(monkeypatch):
    class Broken(OneAnswer):
        def invoke(self, messages):
            raise RuntimeError("secret-value")

    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: Broken())
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 1
    assert "secret-value" not in result.output


def test_interrupt_exit_code(monkeypatch):
    class Interrupted(OneAnswer):
        def invoke(self, messages):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "create_model", lambda **kwargs: Interrupted())
    result = runner.invoke(app, ["task"])
    assert result.exit_code == 130
