from pathlib import Path

import pytest

from miniclaude.commands.registry import CommandContext, build_command_registry


def context(*, active: bool = False) -> CommandContext:
    return CommandContext(
        session_id="abc123def456",
        workspace=Path("workspace").resolve(),
        status="running" if active else "completed",
        route="chat",
        turn_active=active,
        allow_shell=False,
        approval_mode="inline",
        tool_names=("FileReadTool", "BashTool"),
    )


def test_help_lists_initial_commands_without_using_shell():
    result = build_command_registry().execute("/help", context())

    assert result.ok is True
    assert result.action is None
    assert "/status" in result.message
    assert "/approve [mode]" in result.message


def test_full_width_slash_is_normalized_and_arguments_stay_plain_text():
    parsed = build_command_registry().parse("／status anything; Remove-Item secret")

    assert parsed.name == "status"
    assert parsed.arguments == "anything; Remove-Item secret"


def test_unknown_and_oversized_commands_fail_locally():
    registry = build_command_registry()

    assert registry.execute("/missing", context()).ok is False
    assert registry.execute("/" + "x" * 5000, context()).ok is False


def test_mutating_command_is_refused_during_active_turn():
    result = build_command_registry().execute("/new", context(active=True))

    assert result.ok is False
    assert result.action is None
    assert "running" in result.message.lower()


@pytest.mark.parametrize("mode", ["all", "inline", "auto", "deny"])
def test_approve_returns_typed_policy_action(mode):
    result = build_command_registry().execute(f"/approve {mode}", context())

    assert result.ok is True
    assert result.action == f"approval-mode:{mode}"
    assert mode in result.message


def test_approve_without_mode_reports_current_policy():
    result = build_command_registry().execute("/approve", context())

    assert result.ok is True
    assert result.action is None
    assert "disabled" in result.message
    assert "inline" in result.message
    assert all(mode in result.message for mode in ("all", "auto", "deny"))


def test_approvals_is_alias_and_invalid_mode_fails_locally():
    registry = build_command_registry()

    alias = registry.execute("/approvals all", context())
    invalid = registry.execute("/approve forever", context())

    assert alias.action == "approval-mode:all"
    assert invalid.ok is False
    assert invalid.action is None
    assert "all, inline, auto, deny" in invalid.message


def test_approve_cannot_change_policy_during_active_turn():
    result = build_command_registry().execute("/approve auto", context(active=True))

    assert result.ok is False
    assert result.action is None
    assert "running" in result.message.lower()


def test_registry_suggests_matching_commands():
    suggestions = build_command_registry().suggest("/st")

    assert suggestions
    assert suggestions[0].usage == "/status"
