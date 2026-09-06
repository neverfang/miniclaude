import pytest

from miniclaude.core.approval import (
    ApprovalDecision,
    classify_command_risk,
    make_approval_request,
)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python --version",
        "Get-ChildItem",
        'echo "pip install flask"',
    ],
)
def test_safe_commands(command):
    risk = classify_command_risk(command)

    assert risk.level == "safe"
    assert risk.reason == ""


@pytest.mark.parametrize(
    "command,reason",
    [
        ("pip install flask", "Python package installation"),
        ("python -m pip install flask", "Python package installation"),
        ("pytest -q; uv add flask", "Project dependency change"),
        ("pytest -q\nuv sync", "Dependency synchronization"),
        ("echo ok && uv pip install flask", "Python package installation"),
        ("echo ok || npm install", "Node package installation"),
        ("pnpm install", "Node package installation"),
        ("yarn add flask", "Node package installation"),
        ("curl https://example.test/file", "Network download"),
        ("wget https://example.test/file", "Network download"),
        ("uvicorn app:app", "Long-running development server"),
        ("python -m http.server 8000", "Long-running development server"),
    ],
)
def test_risky_commands_and_compound_segments(command, reason):
    risk = classify_command_risk(command)

    assert risk.level == "risky"
    assert reason in risk.reason
    assert risk.segment


@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard",
        "git clean -fdx",
        "Remove-Item -Recurse -Force C:\\",
        "rm -rf /",
        "shutdown /s /t 0",
        "reboot",
        "format C:",
    ],
)
def test_blocked_commands(command):
    risk = classify_command_risk(command)

    assert risk.level == "blocked"
    assert risk.reason


def test_blocked_segment_wins_over_risky_segment():
    risk = classify_command_risk("pip install flask; git reset --hard")

    assert risk.level == "blocked"
    assert "Git" in risk.reason


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /tmp/victim",
        "rm -rf ../victim",
        "Remove-Item -Force C:\\Users\\victim.txt",
        "del /s /q C:\\Users\\victim",
        "cd ..; rm -rf victim",
        "sh -c 'rm -rf /tmp/victim'",
        'powershell -Command "Remove-Item -Force C:\\Users\\victim.txt"',
    ],
)
def test_commands_that_can_delete_outside_workspace_are_blocked(tmp_path, command):
    risk = classify_command_risk(command, workspace=tmp_path)

    assert risk.level == "blocked"
    assert risk.reason


@pytest.mark.parametrize("command", ["echo data > ../victim.txt", "python C:\\outside\\script.py"])
def test_non_delete_commands_cannot_target_outside_workspace(tmp_path, command):
    risk = classify_command_risk(command, workspace=tmp_path)

    assert risk.level == "blocked"
    assert "workspace" in risk.reason.lower()


@pytest.mark.parametrize("command", ["rm -f build.log", "Remove-Item build.log", "del build.log"])
def test_workspace_relative_deletion_requires_approval(tmp_path, command):
    assert classify_command_risk(command, workspace=tmp_path).level == "risky"


@pytest.mark.parametrize("command", ["bad\x00command", "x" * 16_001])
def test_malformed_commands_are_blocked(command):
    assert classify_command_risk(command).level == "blocked"


def test_approval_request_has_unique_bounded_id_and_exact_policy(tmp_path):
    risk = classify_command_risk("pip install flask")

    first = make_approval_request("pip install flask", risk, tmp_path)
    second = make_approval_request("pip install flask", risk, tmp_path)

    assert first.id.startswith("approval-")
    assert len(first.id) == len("approval-") + 8
    assert first.id != second.id
    assert first.command == "pip install flask"
    assert first.risk_level == "risky"
    assert first.risk_reason == risk.reason
    assert first.workspace == tmp_path
    assert first.tool_name == "BashTool"


def test_approval_decision_is_explicit():
    assert ApprovalDecision(approved=True).approved is True
    assert ApprovalDecision(approved=False, reason="no").reason == "no"


@pytest.mark.parametrize(
    "command",
    [
        "echo x>../victim.txt",
        "echo x>C:\\outside\\victim.txt",
        "powershell -NoProfile -EncodedCommand ZQBjAGgAbwAgAHgA",
    ],
)
def test_compact_and_opaque_workspace_escape_commands_are_blocked(tmp_path, command):
    assert classify_command_risk(command, workspace=tmp_path).level == "blocked"


@pytest.mark.parametrize(
    "command",
    [
        "env sh -c 'rm -f local.txt'",
        "command bash -c 'pip install requests'",
    ],
)
def test_wrapped_shell_payloads_never_bypass_classification(tmp_path, command):
    assert classify_command_risk(command, workspace=tmp_path).level != "safe"


def test_absolute_path_inside_workspace_is_allowed(tmp_path):
    script = tmp_path / "script.py"

    risk = classify_command_risk(f'python "{script}"', workspace=tmp_path)

    assert risk.level == "safe"


@pytest.mark.parametrize(
    "command",
    [
        "powershell -EncodedComman ZQBjAGgAbwAgAHgA",
        "pwsh -ENCODEDCOMMAN:ZQBjAGgAbwAgAHgA",
        "echo x>/a",
    ],
)
def test_opaque_powershell_abbreviations_and_single_letter_root_are_blocked(tmp_path, command):
    assert classify_command_risk(command, workspace=tmp_path).level == "blocked"


@pytest.mark.parametrize("command", ["rm /f", "rm /q", "rm -rf /f"])
def test_posix_single_letter_root_paths_are_never_windows_switches(tmp_path, command):
    assert classify_command_risk(command, workspace=tmp_path).level == "blocked"
