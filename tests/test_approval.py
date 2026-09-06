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
