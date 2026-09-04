import json
import os
import time

import pytest

from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.tools.bash_tool import run_bash
from miniclaude.tools.file_tools import edit_file, read_file, write_file
from miniclaude.tools.grep_tool import grep
from miniclaude.tools.registry import build_tools, execute_tool


@pytest.fixture
def state(tmp_path):
    return RuntimeState(tmp_path / "workspace")


def test_create_read_with_line_numbers_and_pagination(state):
    write_file(state, "nested/hello.txt", "一\n二\n三\n")
    result = read_file(state, "nested/hello.txt", offset=1, limit=1)
    assert result["ok"]
    assert result["content"] == "2: 二"
    assert result["total_lines"] == 3
    assert result["truncated"] is True


@pytest.mark.parametrize(
    "path",
    ["../escape", "/absolute", "C:\\outside", "a:stream", ".git/config", ".env", "sub/.env.local"],
)
def test_path_boundaries(state, path):
    with pytest.raises(ToolError):
        write_file(state, path, "no")
    assert not (state.workspace.parent / "escape").exists()


def test_symlink_escape(state, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (state.workspace / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    with pytest.raises(ToolError):
        write_file(state, "link/file", "no")
    assert not (outside / "file").exists()


def test_edit_requires_read_and_detects_external_changes(state):
    path = state.workspace / "hello.py"
    path.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match="read"):
        edit_file(state, "hello.py", "1", "2")
    read_file(state, "hello.py")
    path.write_text("value = 3\n", encoding="utf-8")
    with pytest.raises(ToolError, match="changed"):
        edit_file(state, "hello.py", "3", "2")
    assert path.read_text() == "value = 3\n"


def test_write_requires_read_before_overwrite(state):
    write_file(state, "file", "first")
    with pytest.raises(ToolError, match="read"):
        write_file(state, "file", "second")
    read_file(state, "file")
    write_file(state, "file", "second")
    assert (state.workspace / "file").read_text() == "second"


def test_edit_unique_and_preserves_crlf(state):
    path = state.workspace / "file"
    path.write_bytes(b"one\r\ntwo\r\n")
    read_file(state, "file")
    result = edit_file(state, "file", "two", "three")
    assert result["ok"]
    assert path.read_bytes() == b"one\r\nthree\r\n"


@pytest.mark.parametrize("old", ["", "missing", "a"])
def test_ambiguous_missing_or_empty_edit_is_non_destructive(state, old):
    write_file(state, "file", "a a")
    read_file(state, "file")
    with pytest.raises(ToolError):
        edit_file(state, "file", old, "new")
    assert (state.workspace / "file").read_text() == "a a"


def test_binary_large_file_and_invalid_read_limits(state):
    (state.workspace / "binary").write_bytes(b"\x00hello")
    with pytest.raises(ToolError):
        read_file(state, "binary")
    state.max_file_bytes = 8
    with pytest.raises(ToolError):
        write_file(state, "large", "x" * 9)
    (state.workspace / "large").write_text("x" * 9)
    with pytest.raises(ToolError):
        read_file(state, "large")
    with pytest.raises(ToolError):
        read_file(state, "large", offset=-1)


def test_read_output_is_bounded(state):
    state.max_output_chars = 100
    write_file(state, "long", "x" * 500)
    result = read_file(state, "long")
    assert len(result["content"]) <= 100
    assert result["truncated"]


def test_grep_regex_glob_case_limit_and_exclusions(state):
    write_file(state, "a.py", "Hello 1\nhello 2\n")
    write_file(state, "b.txt", "hello 3")
    (state.workspace / ".env").write_text("hello secret")
    result = grep(state, r"hello \d", glob="*.py", ignore_case=True, head_limit=1)
    assert result["ok"]
    assert len(result["matches"]) == 1
    assert result["matches"][0]["path"] == "a.py"
    assert result["matches"][0]["line"] == 1
    assert result["truncated"]
    all_matches = grep(state, "hello", ignore_case=True)
    assert len(all_matches["matches"]) == 3


def test_grep_invalid_pattern_and_path(state):
    with pytest.raises(ToolError):
        grep(state, "[")
    with pytest.raises(ToolError):
        grep(state, "x", path="../")


def test_shell_disabled_by_default(state):
    with pytest.raises(ToolError, match="allow-shell"):
        run_bash(state, 'python -c "print(5)"')


def test_shell_real_execution_cwd_stdin_and_exit_code(state):
    state.allow_shell = True
    result = run_bash(
        state, 'python -c "import os,sys; print(os.getcwd()); print(sys.stdin.read()); print(2+3)"'
    )
    assert result["ok"]
    assert result["exit_code"] == 0
    assert str(state.workspace) in result["stdout"]
    assert "5" in result["stdout"]
    failed = run_bash(state, 'python -c "import sys; sys.exit(7)"')
    assert failed["ok"] is False
    assert failed["exit_code"] == 7


def test_shell_output_limit_and_timeout(state):
    state.allow_shell = True
    state.max_output_chars = 100
    result = run_bash(state, "python -c \"print('x'*10000)\"")
    assert len(result["stdout"]) <= 100
    assert result["truncated"]
    start = time.monotonic()
    result = run_bash(state, 'python -c "import time; time.sleep(20)"', timeout_seconds=0.4)
    assert result["ok"] is False
    assert result["timed_out"]
    assert time.monotonic() - start < 8


def test_shell_does_not_inherit_model_secrets(state, monkeypatch):
    state.allow_shell = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-trace-secret")
    result = run_bash(
        state,
        "python -c \"import os; print(os.getenv('OPENAI_API_KEY')); "
        "print(os.getenv('LANGSMITH_API_KEY'))\"",
    )
    assert result["ok"]
    assert "secret" not in result["stdout"]
    assert result["stdout"].count("None") == 2


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 601])
def test_invalid_shell_timeout(state, timeout):
    state.allow_shell = True
    with pytest.raises(ToolError):
        run_bash(state, "echo ok", timeout_seconds=timeout)


def test_registry_has_five_typed_tools_and_structured_failures(state):
    tools = build_tools(state)
    assert {tool.name for tool in tools} == {
        "FileReadTool",
        "FileWriteTool",
        "FileEditTool",
        "GrepTool",
        "BashTool",
    }
    for tool in tools:
        assert "state" not in tool.args
        json.dumps(tool.args)
    assert execute_tool(tools, "unknown", {})["ok"] is False
    assert execute_tool(tools, "FileReadTool", {})["ok"] is False
    assert execute_tool(tools, "FileReadTool", {"file_path": "missing"})["ok"] is False
    assert execute_tool(tools, "FileWriteTool", {"file_path": "ok", "content": "yes"})["ok"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell-specific exit status")
def test_shell_native_success_then_cmdlet_failure_is_failure(state):
    state.allow_shell = True
    result = run_bash(state, "python --version; Get-Item -LiteralPath definitely_missing_path")
    assert result["ok"] is False
    assert result["exit_code"] != 0


@pytest.mark.skipif(os.name != "nt", reason="PowerShell-specific encoding")
def test_shell_powershell_unicode_stdout_stderr(state):
    state.allow_shell = True
    result = run_bash(
        state, "Write-Output ([char]0x4e2d); [Console]::Error.WriteLine([char]0x6587)"
    )
    assert result["ok"]
    assert result["stdout"].strip() == "中"
    assert result["stderr"].strip() == "文"


def test_read_pagination_does_not_skip_unreturned_lines(state):
    write_file(state, "file", "one\ntwo\nthree\n")
    state.max_output_chars = 8
    first = read_file(state, "file")
    assert first["content"] == "1: one"
    assert first["lines_returned"] == 1
    assert first["next_offset"] == 1
    second = read_file(state, "file", offset=first["next_offset"])
    assert second["content"] == "2: two"


def test_read_single_long_line_marks_partial_and_retains_offset(state):
    write_file(state, "file", "x" * 100)
    state.max_output_chars = 10
    result = read_file(state, "file")
    assert result["partial_line"] is True
    assert result["next_offset"] == 0


def test_grep_empty_directory_traversal_is_bounded(state, monkeypatch):
    import importlib

    module = importlib.import_module("miniclaude.tools.grep_tool")
    visited = []

    def empty_walk(*args, **kwargs):
        for index in range(5000):
            visited.append(index)
            yield str(state.workspace), [], []

    monkeypatch.setattr(module.os, "walk", empty_walk)
    result = grep(state, "anything")
    assert result["truncated"]
    assert len(visited) <= 2001
